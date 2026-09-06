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

try:
    import sounddevice as _sd
except Exception:
    _sd = None

from ai import add_to_memory, get_ai_response_stream, load_memory
from app_opener import ensure_built
from local_llm import get_active_model_info, get_agent_action, get_clipboard_text, init_local_llm, sanitize_for_tts, _RE_PROBE_GUARD
from reminder_timer import init_reminders
from Searchnow import scrape_web_info
from tool_registry import execute_tool
from tts import (
    speak as tts_speak,
    stop_speaking,
    set_voice,
    get_voice_catalog,
    get_active_voice,
    transcribe_audio_data,
    is_stt_available,
)
import rag_engine
from rag_indexer import start_background_indexer

_ACTIVE_MODE = "3"


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
                stop_speaking()
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
    """Speaks text using unified TTS engine with optional barge-in check."""
    if not text or (interruption_event and interruption_event.is_set()):
        return

    clean_text = sanitize_for_tts(text) or text.strip()
    print(f"Amigo: {clean_text}", flush=True)
    tts_speak(clean_text, block=True)


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
        if is_stt_available():
            try:
                query = transcribe_audio_data(audio)
                if query and query.strip():
                    print(f"[STT Offline] User said: {query}", flush=True)
                    return query.lower()
            except Exception as e:
                print(f"[STT Offline Error]: {e}", flush=True)

        return "None"
    except (sr.WaitTimeoutError, sr.UnknownValueError):
        return "None"
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
    history = rag_engine.get_recent_conversations(15)
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
    stt_info = "works 100% offline via Sherpa-ONNX" if is_stt_available() else "cloud fallback"
    print(f"  [1] Voice (microphone) - {stt_info}")
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
    # Start Alt+V global wake hotkey service
    try:
        from hotkey_service import start_hotkey_service
        start_hotkey_service()
    except Exception as e:
        print(f"    Hotkey service note: {e}")

    speak("Amigo Voice Assistant activated.")

    while True:
        try:
            query = None
            if mode == "1":
                query = take_command()
            elif mode == "2":
                query = input("You: ").strip()
            else:
                query = take_command()
                if not query or query.lower() == "none":
                    query = input("Voice didn't catch that. Type here: ").strip()

            if query and query.lower() != "none":
                q_clean = query.lower().strip().rstrip(".?!")
                if q_clean in ("voices", "list voices", "voice list"):
                    catalog = get_voice_catalog()
                    print("\n--- AVAILABLE 10 OFFLINE VOICES ---")
                    for k, v in catalog.items():
                        current = " [ACTIVE]" if k == get_active_voice() else ""
                        print(f"  • {k:<8} [{v['engine'].title()} - {v['gender']}, {v['accent']}]: {v['desc']}{current}")
                    print("\nSay or type 'voice <name>' (e.g. 'voice ryan' or 'voice bella') to switch.\n")
                    speak(f"You have 10 voices available. Current voice is {get_active_voice()}.")
                    continue
                elif q_clean.startswith("voice ") or q_clean.startswith("switch voice to ") or q_clean.startswith("change voice to "):
                    target = q_clean.replace("switch voice to ", "").replace("change voice to ", "").replace("voice ", "").strip()
                    if target in get_voice_catalog():
                        vname = set_voice(target)
                        speak(f"Voice switched to {vname}.")
                        continue

                process_agent_query(query)

        except KeyboardInterrupt:
            speak("Goodbye!")
            break
        except Exception as e:
            print(f"[Error]: {e}")
            time.sleep(1)
