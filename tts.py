"""
Unified Speech Engine Module for Amigo Voice Assistant.
Handles both Offline Neural Text-to-Speech (TTS via Kokoro ONNX)
and Offline Neural Speech-to-Text (STT via OpenAI Whisper).
"""

import io
import json
import os
import queue
import re
import tarfile
import threading
import time
import urllib.request
import logging
import numpy as np

try:
    import num2words
except ImportError:
    num2words = None

logger = logging.getLogger("amigo.speech")

_kokoro_instance = None
_USE_KOKORO = False

_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "kokoro-onnx"
)
_MODEL_PATH = os.path.join(_MODEL_DIR, "kokoro-v1.0.onnx")
_VOICES_PATH = os.path.join(_MODEL_DIR, "voices-v1.0.bin")

try:
    import sounddevice as _sd
    from kokoro_onnx import Kokoro as _KokoroOnnx

    _USE_KOKORO = True
    logging.getLogger("phonemizer").setLevel(logging.ERROR)
    logger.info("[TTS] Kokoro ONNX available.")
except ImportError:
    logger.warning("[TTS] kokoro-onnx not available.")

# ── 10 Curated Best Offline Studio Neural Voices (Kokoro 24kHz) ────────
CURATED_VOICES = {
    # 5 Best Female Neural Voices
    "nicole":  {"engine": "kokoro", "id": "af_nicole",  "name": "Nicole",  "gender": "Female", "accent": "US", "desc": "Smooth, articulate & studio-clean American female (Recommended)"},
    "sarah":   {"engine": "kokoro", "id": "af_sarah",   "name": "Sarah",   "gender": "Female", "accent": "US", "desc": "Soft, natural & warm American female"},
    "heart":   {"engine": "kokoro", "id": "af_heart",   "name": "Heart",   "gender": "Female", "accent": "US", "desc": "Warm & expressive American female"},
    "sky":     {"engine": "kokoro", "id": "af_sky",     "name": "Sky",     "gender": "Female", "accent": "US", "desc": "Bright, friendly & clear American female"},
    "bella":   {"engine": "kokoro", "id": "af_bella",   "name": "Bella",   "gender": "Female", "accent": "US", "desc": "Energetic & crisp American female"},

    # 5 Best Male Neural Voices
    "adam":    {"engine": "kokoro", "id": "am_adam",    "name": "Adam",    "gender": "Male",   "accent": "US", "desc": "Deep, natural & calm American male baritone"},
    "michael": {"engine": "kokoro", "id": "am_michael", "name": "Michael", "gender": "Male",   "accent": "US", "desc": "Professional, articulate American male"},
    "echo":    {"engine": "kokoro", "id": "am_echo",    "name": "Echo",    "gender": "Male",   "accent": "US", "desc": "Warm & conversational American male"},
    "liam":    {"engine": "kokoro", "id": "am_liam",    "name": "Liam",    "gender": "Male",   "accent": "US", "desc": "Young, natural & clear American male"},
    "george":  {"engine": "kokoro", "id": "bm_george",  "name": "George",  "gender": "Male",   "accent": "GB", "desc": "Distinguished British English gentleman"},
}

_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_profile.json")

ACTIVE_VOICE = "nicole"
KOKORO_VOICE = "af_nicole"
KOKORO_SPEED = 1.05
KOKORO_LANG = "en-us"
_tts_lock = threading.Lock()
_active_stream = None
_active_stream_lock = threading.Lock()


def sync_voice_from_profile() -> str:
    """Synchronizes active voice selection dynamically from amigo_profile.json."""
    global ACTIVE_VOICE, KOKORO_VOICE, KOKORO_LANG
    try:
        if os.path.exists(_PROFILE_PATH):
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
                    if info["engine"] == "kokoro":
                        KOKORO_VOICE = info["id"]
                        KOKORO_LANG = "en-gb" if info.get("accent") == "GB" or info["id"].startswith("b") else "en-us"
                    return v_key
    except Exception as e:
        logger.debug(f"[TTS] Voice profile sync note: {e}")
    return ACTIVE_VOICE


# Initialize active voice from user preference immediately on startup
sync_voice_from_profile()


def set_voice(voice_name: str) -> str:
    """Sets the active voice, updates language, and immediately persists to user profile."""
    global ACTIVE_VOICE, KOKORO_VOICE, KOKORO_LANG
    key = voice_name.lower().strip()
    display_name = voice_name
    if key in CURATED_VOICES:
        ACTIVE_VOICE = key
        info = CURATED_VOICES[key]
        display_name = info["name"]
        if info["engine"] == "kokoro":
            KOKORO_VOICE = info["id"]
            KOKORO_LANG = "en-gb" if info.get("accent") == "GB" or info["id"].startswith("b") else "en-us"
        logger.info(f"[TTS] Active voice switched to: {info['name']} ({info['engine'].title()})")
    else:
        KOKORO_VOICE = voice_name
        ACTIVE_VOICE = voice_name
        display_name = voice_name

    # Persist to profile so voice selection is remembered across restarts
    try:
        pdata = {}
        if os.path.exists(_PROFILE_PATH):
            with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
                pdata = json.load(f)
        pdata.setdefault("preferences", {})["voice"] = key
        pdata.setdefault("ui_settings", {})["voice"] = key
        pdata.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = key
        with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(pdata, f, indent=2)
    except Exception as e:
        logger.debug(f"[TTS] Voice persistence note: {e}")

    return display_name


def get_voice_catalog() -> dict:
    """Returns dictionary of all 10 curated voices with metadata."""
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
    vname = voice_info["name"] if voice_info else KOKORO_VOICE
    return f"Kokoro ONNX Neural ({vname})"


def _get_kokoro():
    """Lazy-load Kokoro ONNX model."""
    global _kokoro_instance
    if _kokoro_instance is None:
        if not os.path.exists(_MODEL_PATH):
            raise FileNotFoundError("Kokoro ONNX model not found at " + _MODEL_PATH)
        logger.info("[TTS] Loading Kokoro ONNX model...")
        _kokoro_instance = _KokoroOnnx(_MODEL_PATH, _VOICES_PATH)
        logger.info("[TTS] Kokoro ONNX ready.")
    return _kokoro_instance


# Pre-compiled regex patterns for zero overhead text sanitization & splitting
_RE_URL = re.compile(r'https?://\S+|www\.\S+')
_RE_SPECIAL_CHARS = re.compile(r'[*#_`~>\[\]()]')
_RE_CONSECUTIVE_DOTS = re.compile(r'\.{2,}')
_RE_WHITESPACE = re.compile(r'\s+')
_RE_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _warmup_kokoro():
    """Warms up Kokoro ONNX in a background thread so module loading is near-instant."""
    if _USE_KOKORO:
        def _warm():
            try:
                kokoro = _get_kokoro()
                with _tts_lock:
                    kokoro.create(
                        "Amigo ready.",
                        voice=KOKORO_VOICE,
                        speed=KOKORO_SPEED,
                        lang=KOKORO_LANG,
                    )
                logger.info("[TTS] Kokoro inference warmed up.")
            except Exception as e:
                logger.debug(f"[TTS Warmup] {e}")

        threading.Thread(target=_warm, daemon=True, name="Kokoro-Warmup").start()


_warmup_kokoro()

# Asynchronous speech worker queue
_speech_queue = queue.Queue()
_speech_generation = 0
_speech_generation_lock = threading.Lock()


def _speech_is_current(generation: int) -> bool:
    with _speech_generation_lock:
        return generation == _speech_generation


def normalize_text_for_tts(text: str) -> str:
    """
    Normalizes raw text into clean, phonetically speakable words:
    - Strips markdown formatting, links, and code symbols
    - Expands currency ($50 -> 50 dollars) and percentages (45% -> 45 percent)
    - Expands dates (e.g. September 6, 2026 -> September sixth, twenty twenty-six)
    - Expands ordinals (1st -> first, 2nd -> second, 6th -> sixth)
    - Expands 4-digit years (2026 -> twenty twenty-six, 1999 -> nineteen ninety-nine)
    - Expands standalone numbers using num2words so phonemizer never encounters raw digits
    - Expands acronyms (VCT -> V. C. T., CPU -> C. P. U., AI -> A. I.)
    """
    if not text:
        return ""
    t = text
    # 1. Remove URLs
    t = _RE_URL.sub('', t)
    # 2. Markdown formatting removal
    t = _RE_SPECIAL_CHARS.sub(' ', t)
    # 3. Hyphenated scores and ranges (e.g. 3-1 -> 3 to 1, 2024-2025 -> 2024 to 2025)
    t = re.sub(r'(\d+)\s*[-–—]\s*(\d+)', r'\1 to \2', t)
    t = re.sub(r'[-–—/|]', ' ', t)
    # 4. Currency and percentages
    t = re.sub(r'\$(\d+(?:\.\d{2})?)', r'\1 dollars', t)
    t = re.sub(r'(\d+)%', r'\1 percent', t)

    if num2words is not None:
        # 4. Dates: e.g. September 6, 2026
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

        # 5. Ordinals (1st, 2nd, 3rd, 4th, 21st)
        def _ord_repl(m):
            try:
                return num2words.num2words(int(m.group(1)), to='ordinal')
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', _ord_repl, t, flags=re.I)

        # 6. Four-digit years (e.g. 1900-2099)
        def _year_repl(m):
            try:
                return num2words.num2words(int(m.group(1)), to='year')
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(19\d\d|20\d\d)\b', _year_repl, t)

        # 7. Other standalone numbers (cardinals)
        def _num_repl(m):
            try:
                return num2words.num2words(int(m.group(1)))
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)\b', _num_repl, t)

    # 8. Acronyms (e.g. VCT, CPU, AI, GPU, API, STT, TTS)
    common_words = {
        'A', 'I', 'IN', 'ON', 'AT', 'TO', 'BY', 'FOR', 'AND', 'THE', 'IS', 'IT', 'US', 'OK',
        'AM', 'PM', 'HE', 'SHE', 'WE', 'MY', 'ME', 'SO', 'NO', 'GO', 'DO', 'IF', 'OR', 'AS'
    }

    def _acronym_repl(m):
        word = m.group(0)
        if word in common_words:
            return word
        return '. '.join(list(word)) + '.'

    t = re.sub(r'\b[A-Z]{2,5}\b', _acronym_repl, t)

    # 9. Clean consecutive dots and whitespace
    t = _RE_CONSECUTIVE_DOTS.sub(', ', t)
    return _RE_WHITESPACE.sub(' ', t).strip()


def _clean_tts_text(text: str) -> str:
    """Cleans and normalizes text for natural neural TTS prosody."""
    return normalize_text_for_tts(text)


def _compact_tts_text(text: str, max_chars: int = 1500) -> str:
    """Keep spoken replies natural without prematurely truncating."""
    clean = _clean_tts_text(text)
    if len(clean) <= max_chars:
        return clean
    sentences = [part.strip() for part in _RE_SENTENCE_SPLIT.split(clean) if part.strip()]
    compact = ""
    for sentence in sentences:
        candidate = f"{compact} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        compact = candidate
    if compact:
        return compact
    return clean[:max_chars].rsplit(" ", 1)[0] + "."



def _process_audio_clarity(samples, sr=24000) -> tuple[np.ndarray, int]:
    """
    Studio audio mastering pipeline:
    - High-fidelity polyphase resampling for non-24kHz sources to prevent WASAPI driver distortion.
    - Preserves natural phoneme decay while trimming silence.
    - Soft anti-click fade ramps.
    - Peak gain normalization to 0.92 (-0.7 dBFS).
    """
    if samples is None or len(samples) == 0:
        return samples, sr
    samples = np.asarray(samples, dtype=np.float32)

    # 1. Resample to 24000 Hz if needed so all speech plays at uniform high-resolution rate
    target_sr = 24000
    if sr != target_sr and len(samples) > 0:
        try:
            import scipy.signal
            from math import gcd
            g = gcd(int(sr), target_sr)
            samples = scipy.signal.resample_poly(samples, target_sr // g, int(sr) // g).astype(np.float32)
            sr = target_sr
        except Exception:
            pass

    # 2. Gentle trailing silence trim
    mask = np.abs(samples) > 0.0002
    if np.any(mask):
        last_idx = int(np.max(np.where(mask)[0]))
        pad_samples = int(0.12 * sr)
        end_idx = min(len(samples), last_idx + pad_samples)
        samples = samples[:end_idx]

    # 3. Fade-in (5ms)
    fade_in_len = min(int(0.005 * sr), len(samples))
    if fade_in_len > 0:
        samples[:fade_in_len] *= np.linspace(0.0, 1.0, fade_in_len, dtype=np.float32)

    # 4. Fade-out (25ms)
    fade_out_len = min(int(0.025 * sr), len(samples))
    if fade_out_len > 0:
        samples[-fade_out_len:] *= np.linspace(1.0, 0.0, fade_out_len, dtype=np.float32)

    # 5. Buffer cushion
    cushion = np.zeros(int(0.04 * sr), dtype=np.float32)
    samples = np.concatenate([samples, cushion])

    # 6. Peak amplitude normalization to 0.92
    peak = float(np.max(np.abs(samples)))
    if peak > 1e-4:
        samples = samples * (0.92 / peak)

    return samples, sr


def _trim_audio_silence(samples, threshold=0.00015, pad_ms=120, sr=24000):
    """Backwards-compatible alias for audio clarity processor."""
    processed, _ = _process_audio_clarity(samples, sr=sr)
    return processed


def _synthesize_and_play_kokoro(kokoro, text, generation):
    """Natural, high-prosody neural speech synthesis without artificial pauses or clipped endings."""
    global _active_stream
    sync_voice_from_profile()
    clean_text = _compact_tts_text(text)
    if not clean_text:
        return

    raw_sentences = [s.strip() for s in _RE_SENTENCE_SPLIT.split(clean_text) if s.strip()]
    if not raw_sentences:
        raw_sentences = [clean_text]

    chunks = []
    current_chunk = []
    current_len = 0
    for s in raw_sentences:
        if current_len + len(s) + 1 > 350 and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [s]
            current_len = len(s)
        else:
            current_chunk.append(s)
            current_len += len(s) + 1
    if current_chunk:
        chunks.append(" ".join(current_chunk))

    if not chunks:
        chunks = [clean_text]

    audio_queue = queue.Queue(maxsize=6)
    sentinel = object()

    def producer():
        try:
            for chunk in chunks:
                if not chunk or not _speech_is_current(generation):
                    break
                spoken_chunk = chunk if chunk.endswith((".", "!", "?", ",", ";", ":")) else chunk + "."
                samp, sr = None, 24000
                voice_info = CURATED_VOICES.get(ACTIVE_VOICE)
                voice_id = voice_info["id"] if voice_info else KOKORO_VOICE
                lang = "en-gb" if (voice_info and voice_info.get("accent") == "GB") or str(voice_id).startswith("b") else "en-us"
                with _tts_lock:
                    samp, sr = kokoro.create(
                        spoken_chunk,
                        voice=voice_id,
                        speed=KOKORO_SPEED,
                        lang=lang,
                    )
                if samp is not None and len(samp) > 0:
                    samp, sr = _process_audio_clarity(samp, sr=sr)
                    while _speech_is_current(generation):
                        try:
                            audio_queue.put((samp, sr), timeout=0.1)
                            break
                        except queue.Full:
                            continue
        except Exception as e:
            logger.error(f"[TTS Stream Error] {e}")
        finally:
            audio_queue.put(sentinel)

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    stream = None
    stream_sr = None
    try:
        while True:
            item = audio_queue.get()
            if item is sentinel or not _speech_is_current(generation):
                break
            samples, sample_rate = item
            if stream is None or stream_sr != sample_rate:
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                stream = _sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")
                stream.start()
                stream_sr = sample_rate
                with _active_stream_lock:
                    _active_stream = stream

            stream.write(samples)
    except Exception as e:
        logger.debug(f"[TTS Playback Note] {e}")
    finally:
        with _active_stream_lock:
            if _active_stream is stream:
                _active_stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


def _speech_worker():
    while True:
        item = _speech_queue.get()
        if isinstance(item, tuple):
            text = item[0] if len(item) > 0 else ""
            generation = item[1] if len(item) > 1 else 0
            request_id = item[2] if len(item) > 2 else None
            broadcast_chat = item[3] if len(item) > 3 else False
        else:
            text, generation, request_id, broadcast_chat = item, 0, None, False
        if not text:
            _speech_queue.task_done()
            continue
        try:
            if not _speech_is_current(generation):
                continue
            tts_started = time.perf_counter()
            if _on_state_change:
                _on_state_change("speaking")
            if broadcast_chat and _on_broadcast:
                _on_broadcast("chat_message", {"sender": "assistant", "text": text})

            try:
                kokoro = _get_kokoro() if _USE_KOKORO else None
                _synthesize_and_play_kokoro(kokoro, text, generation)
            except Exception as e:
                logger.error(f"[TTS] Speech synthesis error: {e}")
        finally:
            logger.info(
                "[Timing] request_id=%s stage=tts duration_ms=%.1f",
                request_id or "unknown",
                (time.perf_counter() - tts_started) * 1000 if "tts_started" in locals() else 0,
            )
            if _on_state_change:
                _on_state_change("idle")
            _speech_queue.task_done()


threading.Thread(target=_speech_worker, daemon=True).start()


def speak(text: str, block: bool = False, request_id: str | None = None, broadcast_chat: bool = False) -> None:
    """Speak text asynchronously, update assistant state, and animate UI with 0ms delay."""
    if not text:
        return
    with _speech_generation_lock:
        generation = _speech_generation
    item = (text, generation, request_id, broadcast_chat)
    if block:
        _speech_queue.put(item)
        _speech_queue.join()
    else:
        _speech_queue.put(item)


def stop_speaking() -> None:
    """Stop current audio and discard speech that has not started yet."""
    global _speech_generation
    with _speech_generation_lock:
        _speech_generation += 1
    while True:
        try:
            _speech_queue.get_nowait()
            _speech_queue.task_done()
        except queue.Empty:
            break
    with _active_stream_lock:
        if _active_stream is not None:
            try:
                _active_stream.abort()
            except Exception:
                pass
    try:
        _sd.stop()
    except Exception:
        pass
    if _on_state_change:
        _on_state_change("idle")


# ────────────────────────────────────────────────────────────────
# ── OPENAI WHISPER OFFLINE SPEECH-TO-TEXT (STT) ENGINE ─────────
# ────────────────────────────────────────────────────────────────

_whisper_model = None
_whisper_model_lock = threading.Lock()
_WHISPER_INIT_ATTEMPTED = False
_WHISPER_IS_AVAILABLE = False
_WHISPER_MODEL_NAME = "base.en"


def get_stt_recognizer():
    """Lazy-loads the OpenAI Whisper offline speech recognizer."""
    global _whisper_model, _WHISPER_INIT_ATTEMPTED, _WHISPER_IS_AVAILABLE
    if _whisper_model is not None:
        return _whisper_model

    with _whisper_model_lock:
        if _whisper_model is not None:
            return _whisper_model
        if _WHISPER_INIT_ATTEMPTED and not _WHISPER_IS_AVAILABLE:
            return None

        _WHISPER_INIT_ATTEMPTED = True
        try:
            import whisper
        except ImportError:
            logger.warning("[STT] openai-whisper not installed.")
            _WHISPER_IS_AVAILABLE = False
            return None

        try:
            t0 = time.time()
            logger.info(f"[STT] Loading OpenAI Whisper ({_WHISPER_MODEL_NAME})...")
            _whisper_model = whisper.load_model(_WHISPER_MODEL_NAME, device="cpu")
            _WHISPER_IS_AVAILABLE = True
            logger.info(f"[STT] OpenAI Whisper ({_WHISPER_MODEL_NAME}) ready in {(time.time() - t0)*1000:.1f}ms.")
            return _whisper_model
        except Exception as e:
            logger.error(f"[STT] Failed to load OpenAI Whisper model: {e}")
            _WHISPER_IS_AVAILABLE = False
            return None


def is_stt_available() -> bool:
    """Returns True if offline STT is initialized and available."""
    return get_stt_recognizer() is not None


def get_stt_engine_name() -> str:
    """Returns descriptive name of active STT engine."""
    if is_stt_available():
        return f"OpenAI Whisper ({_WHISPER_MODEL_NAME})"
    return "Unavailable"


def transcribe_samples(samples: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribes raw float32 audio samples [-1.0, 1.0] completely offline with Whisper."""
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

        # Normalize audio levels if needed
        peak = float(np.max(np.abs(samples))) if len(samples) > 0 else 0
        if peak > 1.0:
            samples = samples / peak

        with _whisper_model_lock:
            result = model.transcribe(
                samples,
                language="en",
                fp16=False,
                temperature=0.0,
                condition_on_previous_text=False,
            )
            return (result.get("text") or "").strip()
    except Exception as e:
        logger.error(f"[STT] Whisper transcription error: {e}")
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
    """Decodes base64-encoded audio data and transcribes it offline with Whisper."""
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
                logger.debug("[STT] OpenAI Whisper STT primed.")
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="WhisperSTT-Warmup").start()


_warmup_stt()

