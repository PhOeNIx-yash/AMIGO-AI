"""
Unified Speech Engine Module for Amigo Voice Assistant.
Handles both Offline Neural Text-to-Speech (TTS via Kokoro & Sherpa Piper)
and Offline Neural Speech-to-Text (STT via Sherpa Moonshine).
"""

import io
import os
import queue
import re
import tarfile
import threading
import time
import urllib.request
import logging
import numpy as np

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
    logger.info("[TTS] Kokoro ONNX available.")
except ImportError:
    logger.warning("[TTS] kokoro-onnx not available.")

_USE_SHERPA = False
try:
    import sherpa_onnx
    _USE_SHERPA = True
    logger.info("[TTS] Sherpa-ONNX available.")
except ImportError:
    logger.warning("[TTS] sherpa-onnx not available.")

# ── 10 Curated Best Offline Voices (5 Kokoro + 5 Sherpa) ────────
CURATED_VOICES = {
    # 5 Best Kokoro Voices (Deep Neural)
    "heart":   {"engine": "kokoro", "id": "af_heart",   "name": "Heart",   "gender": "Female", "accent": "US", "desc": "Warm & natural American female (Default)"},
    "bella":   {"engine": "kokoro", "id": "af_bella",   "name": "Bella",   "gender": "Female", "accent": "US", "desc": "Energetic & clear American female"},
    "adam":    {"engine": "kokoro", "id": "am_adam",    "name": "Adam",    "gender": "Male",   "accent": "US", "desc": "Natural & deep American male"},
    "michael": {"engine": "kokoro", "id": "am_michael", "name": "Michael", "gender": "Male",   "accent": "US", "desc": "Professional & clean American male"},
    "george":  {"engine": "kokoro", "id": "bm_george",  "name": "George",  "gender": "Male",   "accent": "GB", "desc": "Distinguished British English gentleman"},

    # 5 Best Sherpa Voices (Piper VITS INT8)
    "amy":     {"engine": "sherpa", "folder": "vits-piper-en_US-amy-low-int8",     "onnx": "en_US-amy-low.onnx",     "name": "Amy",    "gender": "Female", "accent": "US", "desc": "Natural & friendly American female"},
    "ryan":    {"engine": "sherpa", "folder": "vits-piper-en_US-ryan-low-int8",    "onnx": "en_US-ryan-low.onnx",    "name": "Ryan",   "gender": "Male",   "accent": "US", "desc": "Young conversational American male"},
    "kristin": {"engine": "sherpa", "folder": "vits-piper-en_US-kristin-medium-int8", "onnx": "en_US-kristin-medium.onnx", "name": "Kristin", "gender": "Female", "accent": "US", "desc": "Warm & articulate American female"},
    "joe":     {"engine": "sherpa", "folder": "vits-piper-en_US-joe-medium-int8",  "onnx": "en_US-joe-medium.onnx",  "name": "Joe",    "gender": "Male",   "accent": "US", "desc": "Casual & clear American male"},
    "glados":  {"engine": "sherpa", "folder": "vits-piper-en_US-glados-high-int8", "onnx": "en_US-glados-high.onnx", "name": "GLaDOS", "gender": "Robot",  "accent": "US", "desc": "Iconic Portal AI robotic voice"},
}

ACTIVE_VOICE = "heart"
KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.10
KOKORO_LANG = "en-us"
_tts_lock = threading.Lock()

_SHERPA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "sherpa-onnx")
_sherpa_instances = {}


def _get_sherpa_tts(folder: str, onnx_file: str):
    """Lazy-loads and caches Sherpa-ONNX Piper VITS models."""
    key = (folder, onnx_file)
    if key not in _sherpa_instances:
        vdir = os.path.join(_SHERPA_DIR, folder)
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=os.path.join(vdir, onnx_file),
                    tokens=os.path.join(vdir, "tokens.txt"),
                    data_dir=os.path.join(vdir, "espeak-ng-data"),
                ),
                num_threads=2,
            )
        )
        _sherpa_instances[key] = sherpa_onnx.OfflineTts(cfg)
    return _sherpa_instances[key]


def set_voice(voice_name: str) -> str:
    """Sets the active voice (from the 10 curated aliases or Kokoro voice IDs)."""
    global ACTIVE_VOICE, KOKORO_VOICE
    key = voice_name.lower().strip()
    if key in CURATED_VOICES:
        ACTIVE_VOICE = key
        info = CURATED_VOICES[key]
        if info["engine"] == "kokoro":
            KOKORO_VOICE = info["id"]
        logger.info(f"[TTS] Active voice switched to: {info['name']} ({info['engine'].title()})")
        return info["name"]
    # Fallback to direct Kokoro ID
    KOKORO_VOICE = voice_name
    ACTIVE_VOICE = voice_name
    return voice_name


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
    if voice_info and voice_info.get("engine") == "sherpa":
        return f"Sherpa-ONNX Piper ({voice_info['name']})"
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


def _clean_tts_text(text: str) -> str:
    """Cleans up markdown, links, and special symbols for natural neural TTS prosody."""
    if not text:
        return ""
    t = _RE_URL.sub('', text)
    t = _RE_SPECIAL_CHARS.sub(' ', t)
    t = _RE_CONSECUTIVE_DOTS.sub(', ', t)
    return _RE_WHITESPACE.sub(' ', t).strip()


def _compact_tts_text(text: str, max_chars: int = 700) -> str:
    """Keep spoken replies concise while preserving the full UI response."""
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



def _trim_audio_silence(samples, threshold=0.001, pad_ms=180, sr=24000):
    """Trim excess trailing silence while safely preserving soft trailing consonants, sibilants, and natural decay."""
    if samples is None or len(samples) == 0:
        return samples
    import numpy as np
    mask = np.abs(samples) > threshold
    if not np.any(mask):
        return samples
    last_idx = int(np.max(np.where(mask)[0]))
    pad_samples = int((pad_ms / 1000.0) * sr)
    end_idx = min(len(samples), last_idx + pad_samples)
    trimmed = samples[:end_idx]
    # Add a tiny 60ms cushion so Windows PortAudio/WASAPI hardware buffers do not clip the tail
    cushion = np.zeros(int(0.06 * sr), dtype=samples.dtype)
    return np.concatenate([trimmed, cushion])


def _synthesize_and_play_kokoro(kokoro, text, generation):
    """Natural, high-prosody neural speech synthesis without artificial pauses or clipped endings."""
    clean_text = _compact_tts_text(text)
    if not clean_text:
        return

    raw_sentences = [s.strip() for s in _RE_SENTENCE_SPLIT.split(clean_text) if s.strip()]
    chunks = []
    current_chunk = []
    current_len = 0
    for s in raw_sentences:
        chunk_limit = 220 if not chunks else 380
        if current_len + len(s) + 1 > chunk_limit and current_chunk:
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

    audio_queue = queue.Queue(maxsize=4)
    sentinel = object()

    def producer():
        try:
            for chunk in chunks:
                if not chunk or not _speech_is_current(generation):
                    continue
                # Ensure ending punctuation so synthesizer creates complete phoneme decay
                spoken_chunk = chunk if chunk.endswith((".", "!", "?", ",", ";", ":")) else chunk + "."
                samp, sr = None, 16000
                voice_info = CURATED_VOICES.get(ACTIVE_VOICE)

                if voice_info and voice_info.get("engine") == "sherpa" and _USE_SHERPA:
                    sherpa_tts = _get_sherpa_tts(voice_info["folder"], voice_info["onnx"])
                    with _tts_lock:
                        audio = sherpa_tts.generate(spoken_chunk, sid=0, speed=1.0)
                        samp = np.array(audio.samples, dtype=np.float32)
                        sr = audio.sample_rate
                else:
                    voice_id = voice_info["id"] if voice_info else KOKORO_VOICE
                    with _tts_lock:
                        samp, sr = kokoro.create(
                            spoken_chunk,
                            voice=voice_id,
                            speed=KOKORO_SPEED,
                            lang=KOKORO_LANG,
                        )
                if samp is not None and len(samp) > 0:
                    samp = _trim_audio_silence(samp, threshold=0.001, pad_ms=180, sr=sr)
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

    while True:
        item = audio_queue.get()
        if item is sentinel or not _speech_is_current(generation):
            break
        samples, sample_rate = item
        _sd.play(samples, samplerate=sample_rate)
        _sd.wait()


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
    try:
        _sd.stop()
    except Exception:
        pass
    if _on_state_change:
        _on_state_change("idle")


# ────────────────────────────────────────────────────────────────
# ── SHERPA-ONNX OFFLINE SPEECH-TO-TEXT (STT) ENGINE ────────────
# ────────────────────────────────────────────────────────────────

_stt_recognizer = None
_stt_recognizer_lock = threading.Lock()
_STT_INIT_ATTEMPTED = False
_STT_IS_AVAILABLE = False

_STT_MODEL_DIR = os.path.join(_SHERPA_DIR, "moonshine-tiny-en-quantized")
_STT_ENCODER_PATH = os.path.join(_STT_MODEL_DIR, "encoder_model.ort")
_STT_DECODER_PATH = os.path.join(_STT_MODEL_DIR, "decoder_model_merged.ort")
_STT_TOKENS_PATH = os.path.join(_STT_MODEL_DIR, "tokens.txt")

_STT_DOWNLOAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2"
)


def ensure_stt_model() -> bool:
    """Ensures offline STT model files exist; downloads the ~29MB archive if absent."""
    if (os.path.exists(_STT_ENCODER_PATH) and
        os.path.exists(_STT_DECODER_PATH) and
        os.path.exists(_STT_TOKENS_PATH)):
        return True

    logger.info("[STT] Sherpa-ONNX Moonshine model not found locally. Downloading (~29MB)...")
    try:
        os.makedirs(os.path.dirname(_STT_MODEL_DIR), exist_ok=True)
        archive_path = os.path.join(os.path.dirname(_STT_MODEL_DIR), "stt_download.tar.bz2")
        urllib.request.urlretrieve(_STT_DOWNLOAD_URL, archive_path)

        with tarfile.open(archive_path, "r:bz2") as tar:
            tar.extractall(os.path.dirname(_STT_MODEL_DIR))

        extracted_folder = os.path.join(
            os.path.dirname(_STT_MODEL_DIR),
            "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27",
        )
        if os.path.exists(extracted_folder) and not os.path.exists(_STT_MODEL_DIR):
            os.rename(extracted_folder, _STT_MODEL_DIR)

        if os.path.exists(archive_path):
            os.remove(archive_path)

        logger.info("[STT] Sherpa-ONNX Moonshine model installed successfully.")
        return True
    except Exception as e:
        logger.error(f"[STT] Failed to download Sherpa-ONNX model: {e}")
        return False


def get_stt_recognizer():
    """Lazy-loads the Sherpa-ONNX offline recognizer."""
    global _stt_recognizer, _STT_INIT_ATTEMPTED, _STT_IS_AVAILABLE
    if _stt_recognizer is not None:
        return _stt_recognizer

    with _stt_recognizer_lock:
        if _stt_recognizer is not None:
            return _stt_recognizer
        if _STT_INIT_ATTEMPTED and not _STT_IS_AVAILABLE:
            return None

        _STT_INIT_ATTEMPTED = True
        try:
            import sherpa_onnx
        except ImportError:
            logger.warning("[STT] sherpa-onnx not installed.")
            _STT_IS_AVAILABLE = False
            return None

        if not ensure_stt_model():
            _STT_IS_AVAILABLE = False
            return None

        try:
            t0 = time.time()
            _stt_recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
                encoder=_STT_ENCODER_PATH,
                decoder=_STT_DECODER_PATH,
                tokens=_STT_TOKENS_PATH,
                num_threads=2,
            )
            _STT_IS_AVAILABLE = True
            logger.info(f"[STT] Sherpa-ONNX Moonshine ready in {(time.time() - t0)*1000:.1f}ms.")
            return _stt_recognizer
        except Exception as e:
            logger.error(f"[STT] Failed to load Sherpa-ONNX recognizer: {e}")
            _STT_IS_AVAILABLE = False
            return None


def is_stt_available() -> bool:
    """Returns True if offline STT is initialized and available."""
    return get_stt_recognizer() is not None


def get_stt_engine_name() -> str:
    """Returns descriptive name of active STT engine."""
    if is_stt_available():
        return "Sherpa-ONNX Moonshine (Offline INT8)"
    return "Unavailable"


def transcribe_samples(samples: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribes raw float32 audio samples [-1.0, 1.0] completely offline."""
    rec = get_stt_recognizer()
    if rec is None or samples is None or len(samples) == 0:
        return ""

    with _stt_recognizer_lock:
        stream = rec.create_stream()
        stream.accept_waveform(sample_rate, samples.astype(np.float32))
        rec.decode_stream(stream)
        return stream.result.text.strip()


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
    """Transcribes audio bytes from uploaded WAV, WebM, or PCM buffer."""
    if not audio_bytes:
        return ""
    try:
        import soundfile as sf
        with io.BytesIO(audio_bytes) as bio:
            audio, sr = sf.read(bio, dtype="float32")
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            return transcribe_samples(audio, sr)
    except Exception:
        try:
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return transcribe_samples(samples, 16000)
        except Exception as e2:
            logger.error(f"[STT] Raw audio byte transcription failed: {e2}")
            return ""


# Warm up STT in background thread
def _warmup_stt():
    def _run():
        try:
            rec = get_stt_recognizer()
            if rec:
                dummy = np.zeros(1600, dtype=np.float32)
                transcribe_samples(dummy, 16000)
                logger.debug("[STT] Sherpa-ONNX STT primed.")
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="SherpaSTT-Warmup").start()


_warmup_stt()

