"""
TTS Engine Module for Amigo Voice Assistant.
Priority: Kokoro ONNX (Neural) -> win32com SAPI -> pyttsx3 fallback.
Provides asynchronous speech worker queue with sentence chunking, silence trimming, and instant interruption.
"""

import os
import queue
import re
import threading
import time
import logging

logger = logging.getLogger("amigo.tts")

_kokoro_instance = None
_USE_KOKORO = False
_USE_WIN32 = False
_tts_engine = None

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
    logger.info("[TTS] kokoro-onnx not available, trying win32com SAPI...")
    try:
        import win32com.client as _win32

        _sapi = _win32.Dispatch("SAPI.SpVoice")
        _sapi.Rate = 1
        _USE_WIN32 = True
        logger.info("[TTS] Using win32com SAPI SpVoice.")
    except Exception:
        logger.info("[TTS] win32com not available, falling back to pyttsx3")
        try:
            import pyttsx3 as _pyttsx3
        except Exception:
            pass

KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.10
KOKORO_LANG = "en-us"
_tts_lock = threading.Lock()

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
    if _USE_KOKORO:
        return "Kokoro ONNX (Neural)"
    if _USE_WIN32:
        return "Windows Native SAPI"
    return "pyttsx3"


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
                # Ensure ending punctuation so Kokoro synthesizes complete phoneme decay
                spoken_chunk = chunk if chunk.endswith((".", "!", "?", ",", ";", ":")) else chunk + "."
                with _tts_lock:
                    samp, sr = kokoro.create(
                        spoken_chunk,
                        voice=KOKORO_VOICE,
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

            if _USE_KOKORO:
                try:
                    kokoro = _get_kokoro()
                    _synthesize_and_play_kokoro(kokoro, text, generation)
                    continue
                except Exception as e:
                    logger.error(f"[TTS] Kokoro error: {e} — falling back")

            if _USE_WIN32:
                try:
                    _sapi.Speak(text)
                    continue
                except Exception as e:
                    logger.error(f"[TTS] SAPI error: {e}")

            global _tts_engine
            try:
                if _tts_engine is None:
                    _tts_engine = _pyttsx3.init("sapi5")
                    voices = _tts_engine.getProperty("voices")
                    if voices:
                        _tts_engine.setProperty("voice", voices[0].id)
                _tts_engine.say(text)
                _tts_engine.runAndWait()
            except Exception as e:
                logger.error(f"[TTS] pyttsx3 error: {e}")
                _tts_engine = None
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
    if _USE_KOKORO:
        try:
            _sd.stop()
        except Exception:
            pass
    if _USE_WIN32:
        try:
            _sapi.Speak("", 2)
        except Exception:
            pass
    if _tts_engine is not None:
        try:
            _tts_engine.stop()
        except Exception:
            pass
    if _on_state_change:
        _on_state_change("idle")
