"""
Main Entrypoint for Amigo Voice Assistant.
Coordinates voice/type input, LLM agent action dispatching, and Kokoro/SAPI TTS playback.
"""

import os
import queue
import re
import threading
import time
import numpy as np
import speech_recognition as sr

# TTS Engines
_USE_KOKORO = False
_USE_WIN32 = False
_sd = None
_KokoroOnnx = None

try:
    import sounddevice as _sd
    from kokoro_onnx import Kokoro as _KokoroOnnx
    _USE_KOKORO = True
except Exception:
    _USE_KOKORO = False

from ai import add_to_memory, get_ai_response_stream, load_memory
from app_opener import ensure_built
from local_llm import get_active_model_info, get_agent_action, get_clipboard_text, init_local_llm, sanitize_for_tts, _RE_PROBE_GUARD
from reminder_timer import init_reminders
from Searchnow import scrape_web_info
from tool_registry import execute_tool
import rag_engine
from rag_indexer import start_background_indexer

_kokoro_instance = None
_tts_engine = None
_sapi = None

_KOKORO_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "kokoro-onnx")
_KOKORO_MODEL_PATH = os.path.join(_KOKORO_MODEL_DIR, "kokoro-v1.0.onnx")
_KOKORO_VOICES_PATH = os.path.join(_KOKORO_MODEL_DIR, "voices-v1.0.bin")

if _USE_KOKORO:
    print("[TTS] Kokoro ONNX available.")
else:
    try:
        import win32com.client as _win32
        _sapi = _win32.Dispatch("SAPI.SpVoice")
        _sapi.Rate = 1
        _USE_WIN32 = True
        print("[TTS] Using win32com SAPI SpVoice.")
    except Exception:
        try:
            import pyttsx3 as _pyttsx3
            _tts_engine = _pyttsx3.init("sapi5")
        except Exception:
            print("[TTS] Warning: No TTS engine available!")

KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.15
KOKORO_LANG = "en-us"
_tts_cache = {}
_ACTIVE_MODE = "3"


def _get_kokoro():
    """Lazy-load the Kokoro ONNX model once."""
    global _kokoro_instance
    if _kokoro_instance is None:
        if not os.path.exists(_KOKORO_MODEL_PATH):
            raise FileNotFoundError(f"Kokoro model not found at {_KOKORO_MODEL_PATH}")
        _kokoro_instance = _KokoroOnnx(_KOKORO_MODEL_PATH, _KOKORO_VOICES_PATH)
    return _kokoro_instance


class BargeInMonitor:
    """Detects user voice interruption during assistant audio output."""
    def __init__(self, interruption_event: threading.Event, threshold: float = 0.22):
        self.interruption_event = interruption_event
        self.threshold = threshold
        self.stream = None
        self._consecutive_hits = 0
        self._start_time = 0

    def _audio_callback(self, indata, frames, time_info, status):
        if self.interruption_event.is_set() or time.time() - self._start_time < 0.35:
            return
        energy = float(np.sqrt(np.mean(indata**2)))
        if energy > self.threshold:
            self._consecutive_hits += 1
            if self._consecutive_hits >= 4:
                self.interruption_event.set()
                if _sd:
                    try:
                        _sd.stop()
                    except Exception:
                        pass
        else:
            self._consecutive_hits = max(0, self._consecutive_hits - 1)

    def start(self):
        if _sd is None or _ACTIVE_MODE == "2":
            return
        try:
            self._start_time = time.time()
            self._consecutive_hits = 0
            self.stream = _sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=1024, callback=self._audio_callback)
            self.stream.start()
        except Exception:
            self.stream = None

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


def speak(text: str, interruption_event: threading.Event | None = None) -> None:
    """Speaks text using Kokoro ONNX, Win32 SAPI, or Pyttsx3."""
    if not text or (interruption_event and interruption_event.is_set()):
        return

    clean_text = sanitize_for_tts(text) or text.strip()
    print(f"Amigo: {clean_text}", flush=True)

    if _USE_KOKORO:
        try:
            kokoro = _get_kokoro()
            clean_text = re.sub(r'[*#_`~>\[\]()]', ' ', clean_text)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()

            cache_key = clean_text.lower()
            if cache_key in _tts_cache:
                samples, sample_rate = _tts_cache[cache_key]
            else:
                samples, sample_rate = kokoro.create(clean_text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG)
                silence_pad = np.zeros(int(0.20 * sample_rate), dtype=samples.dtype)
                samples = np.concatenate([samples, silence_pad])
                if len(clean_text) < 120 and len(_tts_cache) < 20:
                    _tts_cache[cache_key] = (samples, sample_rate)

            _sd.play(samples, samplerate=sample_rate)
            _sd.wait()
            return
        except Exception as e:
            print(f"[TTS] Kokoro error: {e}")

    if _USE_WIN32:
        try:
            _sapi.Speak(clean_text)
            return
        except Exception:
            pass

    if _tts_engine:
        try:
            _tts_engine.say(clean_text)
            _tts_engine.runAndWait()
        except Exception:
            pass


def speak_stream(sentence_generator, interruption_event: threading.Event | None = None) -> str:
    """Streams sentence generation with barge-in voice interruption."""
    if interruption_event is None:
        interruption_event = threading.Event()

    monitor = BargeInMonitor(interruption_event)
    monitor.start()
    collected = []
    first_printed = False

    try:
        for sentence in sentence_generator:
            if interruption_event.is_set():
                break
            clean_text = sanitize_for_tts(sentence)
            if not clean_text:
                continue

            collected.append(clean_text)
            if not first_printed:
                print(f"Amigo: {clean_text}", end=" ", flush=True)
                first_printed = True
            else:
                print(f"{clean_text}", end=" ", flush=True)

            speak(clean_text, interruption_event=interruption_event)

        if first_printed:
            print(flush=True)
        return " ".join(collected).strip()
    finally:
        monitor.stop()


_recognizer = sr.Recognizer()
_recognizer.energy_threshold = 350
_recognizer.dynamic_energy_threshold = True
_recognizer.pause_threshold = 0.95
_mic_calibrated = False


def take_command(timeout=8, phrase_time_limit=15) -> str:
    """Listens to user microphone and returns recognized text."""
    global _mic_calibrated
    try:
        with sr.Microphone() as source:
            if not _mic_calibrated:
                _recognizer.adjust_for_ambient_noise(source, duration=0.3)
                _recognizer.energy_threshold = max(150, min(_recognizer.energy_threshold, 3000))
                _mic_calibrated = True

            print("Listening...", flush=True)
            audio = _recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)

        print("Recognizing...", flush=True)
        query = _recognizer.recognize_google(audio, language="en-US")
        print(f"User said: {query}", flush=True)
        return query.lower()
    except (sr.WaitTimeoutError, sr.UnknownValueError):
        return "None"
    except sr.RequestError:
        print("[Offline Mode] Voice recognition offline.", flush=True)
        return "OFFLINE_ERROR"
    except Exception as e:
        print(f"[Speech Note]: {e}", flush=True)
        return "None"


def _execute_action(action: dict, query: str, spoken_so_far: list, interruption_event: threading.Event | None = None) -> str:
    """Executes a single tool action and returns spoken text."""
    tool = action.get("tool", "chat")
    params = action.get("params", {})
    spoken = action.get("speak", "")

    if interruption_event and interruption_event.is_set():
        return ""

    # Streaming conversation / live web search answer
    if tool == "chat" or (tool == "web_search" and not spoken):
        snippets = scrape_web_info(params.get("query", query)) if tool == "web_search" else None
        gen = get_ai_response_stream(query, web_context=snippets, interruption_event=interruption_event)
        spoken = speak_stream(gen, interruption_event=interruption_event)
    else:
        spoken, url, meta = execute_tool(tool, params, query=query, spoken=spoken)
        if spoken:
            speak(spoken, interruption_event=interruption_event)
        if tool == "exit":
            exit(0)

    return spoken


def process_agent_query(query: str) -> None:
    """Processes user voice query through agent actions and stores context in memory."""
    memory = load_memory()
    history = memory.get("conversations", [])[-15:]
    clipboard_used = bool(get_clipboard_text())

    actions = [{"tool": "chat", "params": {}, "speak": ""}] if _RE_PROBE_GUARD.search(query) else get_agent_action(query, conversation_history=history)

    combined_spoken = []
    last_tool = "chat"
    interruption_event = threading.Event()

    for action in actions:
        if interruption_event.is_set():
            break
        spoken = _execute_action(action, query, combined_spoken, interruption_event=interruption_event)
        if spoken:
            combined_spoken.append(spoken)
        last_tool = action.get("tool", "chat")

    final_reply = " ".join(combined_spoken).strip() or f"Completed {last_tool.replace('_', ' ')}."
    add_to_memory(query, final_reply, tool=last_tool, clipboard_used=clipboard_used)


if __name__ == "__main__":
    _model_info = get_active_model_info()
    print("==========================================")
    print(f"   AMIGO VOICE ASSISTANT - {_model_info['name'].upper()}")
    print("   [ AGENTIC AI MODE ENABLED ]")
    print("==========================================\n")
    print("  Choose input mode:")
    print("  [1] Voice (microphone) - needs internet")
    print("  [2] Type  (keyboard)   - works offline")
    print("  [3] Both  (voice + type fallback)\n")

    mode = input("  Enter 1, 2, or 3 (default=3): ").strip()
    if mode not in ("1", "2", "3"):
        mode = "3"
    _ACTIVE_MODE = mode

    print(f"\n[ AMIGO AI ] Initializing Local AI Engine ({_model_info['name']})...")
    init_local_llm()
    ensure_built()
    init_reminders(speak_callback=speak)

    print("[ AMIGO ] Initializing RAG memory engine...", flush=True)
    rag_engine.init_rag()
    start_background_indexer(rag_engine, interval_minutes=30)
    print("[ AMIGO ] RAG engine ready. Background indexer started.", flush=True)

    speak("Amigo Voice Assistant activated.")

    while True:
        try:
            query = None
            if mode == "1":
                query = take_command()
                if query == "OFFLINE_ERROR":
                    query = input("\n[ OFFLINE MODE ] Type your command: ").strip()
            elif mode == "2":
                query = input("You: ").strip()
            else:
                query = take_command()
                if query == "OFFLINE_ERROR":
                    query = input("\n[ OFFLINE MODE ] Type your command: ").strip()
                elif not query or query.lower() == "none":
                    query = input("Voice didn't catch that. Type here: ").strip()

            if query and query.lower() not in ("none", "offline_error"):
                process_agent_query(query)

        except KeyboardInterrupt:
            speak("Goodbye!")
            break
        except Exception as e:
            print(f"[Error]: {e}")
            time.sleep(1)
