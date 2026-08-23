import datetime
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import numpy as np
import psutil
import speech_recognition as sr
import wikipedia

# Import sounddevice and kokoro_onnx before C++ llama_cpp initialization
_USE_KOKORO = False
_USE_WIN32 = False
_sd = None
_KokoroOnnx = None
_kokoro_init_err = None

try:
    import sounddevice as _sd
    from kokoro_onnx import Kokoro as _KokoroOnnx
    _USE_KOKORO = True
except Exception as _e_kokoro:
    _USE_KOKORO = False
    _kokoro_init_err = _e_kokoro

import os_automation
from ai import (
    add_to_memory,
    get_ai_response,
    get_ai_response_stream,
    load_memory,
    set_last_screen_text,
)
from app_opener import (
    ensure_built,
    find_files,
    open_file_or_location,
    open_folder,
    open_windows_app,
)
from Calculatenumbers import Calc
from local_llm import (
    get_active_model_info,
    get_agent_action,
    get_clipboard_text,
    init_local_llm,
    sanitize_for_tts,
    _RE_PROBE_GUARD,
)
from screen_vision import capture_screen_image, get_screen_vision_context
from Searchnow import scrape_web_info, searchGoogle, searchYoutube
from weather import weather_command
from reminder_timer import (
    init_reminders,
    handle_set_timer,
    handle_set_reminder,
    handle_list_reminders,
    handle_cancel_reminder,
)

try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None

try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except ImportError:
    AudioUtilities = None


_kokoro_instance = None  # lazy-loaded on first speak()
_tts_engine = None
_pyttsx3 = None
_sapi = None

# Model files location (downloaded once)
_KOKORO_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "kokoro-onnx"
)
_KOKORO_MODEL_PATH = os.path.join(_KOKORO_MODEL_DIR, "kokoro-v1.0.onnx")
_KOKORO_VOICES_PATH = os.path.join(_KOKORO_MODEL_DIR, "voices-v1.0.bin")

if _USE_KOKORO:
    print("[TTS] Kokoro ONNX (fast) available.")
else:
    if _kokoro_init_err:
        print(f"[TTS] kokoro-onnx not available ({_kokoro_init_err}), trying win32com SAPI ...")
    try:
        import win32com.client as _win32

        _sapi = _win32.Dispatch("SAPI.SpVoice")
        _sapi.Rate = 1
        _USE_WIN32 = True
        print("[TTS] Using win32com SAPI SpVoice.")
    except Exception as _e2:
        print(f"[TTS] win32com not available ({_e2}), falling back to pyttsx3")
        try:
            # pyrefly: ignore [missing-import]
            import pyttsx3 as _pyttsx3
        except ImportError:
            print("[TTS] WARNING: No TTS engine available!")


# ── Kokoro voice settings ──────────────────────────────────────────────────
# Voices: af_heart, af_sarah, am_adam, am_michael, bf_emma, bm_daniel
KOKORO_VOICE = "af_heart"  # ← change voice here
KOKORO_SPEED = 1.15  # 1.0 = normal; higher = faster speech
KOKORO_LANG = "en-us"  # en-us or en-gb


# TTS audio cache for common phrases (avoids re-synthesis)
_tts_cache = {}
_TTS_CACHE_MAX = 20


def _get_kokoro():
    """Lazy-load the Kokoro ONNX model (stays in memory after first load)."""
    global _kokoro_instance
    if _kokoro_instance is None:
        if not os.path.exists(_KOKORO_MODEL_PATH):
            raise FileNotFoundError(
                f"Kokoro ONNX model not found at {_KOKORO_MODEL_PATH}"
            )
        print("[TTS] Loading Kokoro ONNX model ...")
        _kokoro_instance = _KokoroOnnx(_KOKORO_MODEL_PATH, _KOKORO_VOICES_PATH)
        print("[TTS] Kokoro ONNX ready.")
    return _kokoro_instance


_ACTIVE_MODE = "3"  # "1" = voice, "2" = type, "3" = voice+type


class BargeInMonitor:
    """
    Lightweight audio listener that detects when the user begins speaking
    during assistant audio playback, signaling an immediate interruption.
    Uses calibrated energy gating to avoid false triggers from speaker output.
    """
    def __init__(self, interruption_event: threading.Event, threshold: float = 0.22):
        self.interruption_event = interruption_event
        self.threshold = threshold
        self.stream = None
        self.is_running = False
        self._consecutive_hits = 0
        self._start_time = 0

    def _audio_callback(self, indata, frames, time_info, status):
        if self.interruption_event.is_set():
            return
        # Ignore initial 0.35s onset of speaker audio
        if time.time() - self._start_time < 0.35:
            return

        energy = float(np.sqrt(np.mean(indata**2)))
        if energy > self.threshold:
            self._consecutive_hits += 1
            if self._consecutive_hits >= 4:  # ~250ms of sustained intentional user voice
                self.interruption_event.set()
                if _sd is not None:
                    try:
                        _sd.stop()
                    except Exception:
                        pass
        else:
            self._consecutive_hits = max(0, self._consecutive_hits - 1)

    def start(self):
        global _ACTIVE_MODE
        # In Keyboard/Type mode (mode 2), mic barge-in is disabled to prevent accidental triggers
        if _sd is None or _ACTIVE_MODE == "2":
            return
        try:
            self.is_running = True
            self._start_time = time.time()
            self._consecutive_hits = 0
            self.stream = _sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=self._audio_callback,
            )
            self.stream.start()
        except Exception:
            self.stream = None

    def stop(self):
        self.is_running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


def speak(text, interruption_event: threading.Event | None = None):
    if not text:
        return

    if interruption_event and interruption_event.is_set():
        return

    clean_text = sanitize_for_tts(text)
    if not clean_text:
        clean_text = text.strip()

    print(f"Amigo: {clean_text}", flush=True)

    if _USE_KOKORO:
        try:
            kokoro = _get_kokoro()
            sample_rate = 24000

            # Subdivide long sentences to stay within Kokoro's 510 phoneme context
            chunks = [clean_text] if len(clean_text) <= 240 else []
            if len(clean_text) > 240:
                rem = clean_text
                while len(rem) > 240:
                    split_idx = max(rem.rfind(c, 0, 240) for c in [". ", ", ", "; ", ": ", " "])
                    if split_idx <= 0:
                        split_idx = 240
                    part = rem[:split_idx].strip()
                    if part:
                        chunks.append(part)
                    rem = rem[split_idx:].strip()
                if rem:
                    chunks.append(rem)

            for sub_text in chunks:
                if interruption_event and interruption_event.is_set():
                    _sd.stop()
                    return

                cache_key = sub_text.lower()
                if cache_key in _tts_cache:
                    samples, sample_rate = _tts_cache[cache_key]
                else:
                    samples, sample_rate = kokoro.create(
                        sub_text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG
                    )
                    if len(sub_text) < 120 and len(_tts_cache) < _TTS_CACHE_MAX:
                        _tts_cache[cache_key] = (samples, sample_rate)

                _sd.stop()
                _sd.play(samples, samplerate=sample_rate)
                duration = len(samples) / float(sample_rate)
                start_time = time.time()
                while time.time() - start_time < duration:
                    if interruption_event and interruption_event.is_set():
                        _sd.stop()
                        return
                    time.sleep(0.02)

            time.sleep(0.02)
            return
        except Exception as e:
            print(f"[TTS] Kokoro ONNX error: {e} — falling back to SAPI")

    if _USE_WIN32:
        try:
            _sapi.Speak(clean_text)
            return
        except Exception as e:
            print(f"[TTS] SAPI error: {e}")

    if _pyttsx3 is not None:
        try:
            if _tts_engine is None:
                _tts_engine = _pyttsx3.init("sapi5")
                voices = _tts_engine.getProperty("voices")
                if voices:
                    _tts_engine.setProperty("voice", voices[0].id)
            _tts_engine.say(clean_text)
            _tts_engine.runAndWait()
        except Exception as e:
            print(f"[TTS] pyttsx3 error: {e}")
            _tts_engine = None


def speak_stream(sentence_generator, interruption_event: threading.Event | None = None) -> str:
    """
    Real-time streaming speech playback with parallel synthesis and voice interruption (barge-in).
    Synthesizes and plays sentence chunks as they stream from the LLM.
    Returns the complete accumulated spoken text.
    """
    if interruption_event is None:
        interruption_event = threading.Event()

    monitor = BargeInMonitor(interruption_event)
    monitor.start()

    collected_sentences = []
    first_printed = False

    try:
        if _USE_KOKORO:
            kokoro = _get_kokoro()
            audio_queue = queue.Queue(maxsize=4)

            def _synthesis_worker():
                try:
                    for sentence in sentence_generator:
                        if interruption_event.is_set():
                            break
                        clean_text = sanitize_for_tts(sentence)
                        if not clean_text:
                            continue

                        cache_key = clean_text.lower()
                        if cache_key in _tts_cache:
                            samples, sr = _tts_cache[cache_key]
                        else:
                            samples, sr = kokoro.create(
                                clean_text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG
                            )
                            if len(clean_text) < 120 and len(_tts_cache) < _TTS_CACHE_MAX:
                                _tts_cache[cache_key] = (samples, sr)

                        audio_queue.put((clean_text, samples, sr))
                        if interruption_event.is_set():
                            break
                except Exception:
                    pass
                finally:
                    audio_queue.put(None)  # Sentinel

            synth_thread = threading.Thread(target=_synthesis_worker, daemon=True)
            synth_thread.start()

            while not interruption_event.is_set():
                try:
                    item = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    if not synth_thread.is_alive():
                        break
                    continue

                if item is None:
                    break

                clean_text, samples, sr = item
                collected_sentences.append(clean_text)

                if not first_printed:
                    print(f"Amigo: {clean_text}", end=" ", flush=True)
                    first_printed = True
                else:
                    print(f"{clean_text}", end=" ", flush=True)

                _sd.stop()
                _sd.play(samples, samplerate=sr)

                duration = len(samples) / float(sr)
                start_time = time.time()
                while time.time() - start_time < duration:
                    if interruption_event.is_set():
                        _sd.stop()
                        print("\n[Voice Interrupted]", flush=True)
                        return " ".join(collected_sentences).strip()
                    time.sleep(0.02)

            if first_printed:
                print(flush=True)

            return " ".join(collected_sentences).strip()

        # Fallback TTS engines (SAPI / pyttsx3)
        for sentence in sentence_generator:
            if interruption_event.is_set():
                break
            clean_text = sanitize_for_tts(sentence)
            if not clean_text:
                continue

            collected_sentences.append(clean_text)
            if not first_printed:
                print(f"Amigo: {clean_text}", end=" ", flush=True)
                first_printed = True
            else:
                print(f"{clean_text}", end=" ", flush=True)

            speak(clean_text, interruption_event=interruption_event)

        if first_printed:
            print(flush=True)

        return " ".join(collected_sentences).strip()

    finally:
        monitor.stop()


_recognizer = sr.Recognizer()
_recognizer.energy_threshold = 350
_recognizer.dynamic_energy_threshold = True
_recognizer.dynamic_energy_adjustment_damping = 0.15
_recognizer.dynamic_energy_ratio = 1.5
_recognizer.pause_threshold = 0.95  # Perfectly tuned 950ms pause threshold (fast & responsive)
_recognizer.phrase_threshold = 0.25
_recognizer.non_speaking_duration = 0.6
_mic_calibrated = False


def take_command(timeout=8, phrase_time_limit=15):
    """
    High-quality speech recognition:
    - Auto-calibrates ambient noise (bounded 150-3000) for background noise rejection.
    - Ultra-fast 0.75s pause threshold for zero speech-to-intent latency.
    - Locale set to en-US for maximum recognition accuracy.
    - Catch-all exception handling keeps speech loop smooth and uninterrupted.
    """
    global _mic_calibrated

    try:
        with sr.Microphone() as source:
            if not _mic_calibrated:
                print("Calibrating ambient noise...", flush=True)
                _recognizer.adjust_for_ambient_noise(source, duration=0.3)
                _recognizer.energy_threshold = max(150, min(_recognizer.energy_threshold, 3000))
                _mic_calibrated = True

            print("Listening...", flush=True)
            audio = _recognizer.listen(
                source, timeout=timeout, phrase_time_limit=phrase_time_limit
            )

        print("Recognizing...", flush=True)
        query = _recognizer.recognize_google(audio, language="en-US")

        print(f"User said: {query}", flush=True)
        return query.lower()

    except sr.WaitTimeoutError:
        return "None"

    except sr.UnknownValueError:
        return "None"

    except sr.RequestError:
        print("[Offline Mode] Internet disconnected for voice recognition.", flush=True)
        return "OFFLINE_ERROR"

    except Exception as e:
        print(f"[Speech Note]: {e}", flush=True)
        return "None"



def _execute_action(action: dict, query: str, spoken_so_far: list, interruption_event: threading.Event | None = None) -> str:
    """
    Execute a single action dict and return the spoken reply (if any).
    spoken_so_far accumulates speech across multi-action sequences so memory
    gets a meaningful combined reply at the end.
    """
    tool     = action.get("tool", "chat")
    params   = action.get("params", {})
    spoken   = action.get("speak", "")

    if interruption_event and interruption_event.is_set():
        return ""

    if tool == "web_search":
        search_q = params.get("query", query).strip() or query
        try:
            from ai import update_active_state
            update_active_state("last_search", {"query": search_q})
        except Exception:
            pass
        snippets = scrape_web_info(search_q)
        sentence_gen = (
            get_ai_response_stream(query, web_context=snippets, interruption_event=interruption_event)
            if snippets
            else get_ai_response_stream(query, interruption_event=interruption_event)
        )
        spoken = speak_stream(sentence_gen, interruption_event=interruption_event)
        if any(kw in query.lower() for kw in ("google", "browser", "open google", "search google", "show in browser")):
            searchGoogle(search_q)

    elif tool == "open_website":
        url = params.get("url", "")
        if url:
            if not url.startswith("http"):
                url = "https://" + url
            webbrowser.open(url)
            try:
                from ai import update_active_state
                update_active_state("active_subject", {"name": url, "category": "website"})
            except Exception:
                pass
        else:
            searchGoogle(query)
        if spoken:
            speak(spoken)

    elif tool == "show_images":
        q = params.get("query", query)
        encoded_q = urllib.parse.quote(q)
        webbrowser.open(f"https://www.google.com/search?q={encoded_q}")
        try:
            from ai import update_active_state
            update_active_state("last_search", {"query": f"Images of {q}"})
        except Exception:
            pass
        if spoken:
            speak(spoken)

    elif tool == "play_youtube":
        q = params.get("query", query)
        if q.lower().strip() in ["music", "some music", "a song", "songs", "play music"]:
            try:
                from ai import load_memory
                _favs = load_memory().get("user_profile", {}).get("preferences", {}).get("favorite_artists", [])
                q = f"{_favs[0]} songs playlist" if _favs else "popular hit songs playlist"
            except Exception:
                q = "popular hit songs playlist"

        title = q
        artist = ""
        if " by " in q.lower():
            p = re.split(r"\s+by\s+", q, maxsplit=1, flags=re.IGNORECASE)
            title = p[0].strip()
            artist = p[1].strip()

        try:
            from ai import update_active_state
            update_active_state("current_media", {
                "title": title,
                "artist": artist,
                "platform": "YouTube",
                "query": q,
            })
        except Exception:
            pass

        clean_q = re.sub(
            r"^(?:play\s+music|play\s+song|play\s+the\s+song|play\s+the\s+track|play\s+some\s+music|play)\s+",
            "",
            q.strip(),
            flags=re.IGNORECASE,
        ).strip() or q.strip()

        try:
            is_live = "live" in clean_q.lower()
            sp_filter = "EgJAAQ%253D%253D" if is_live else "EgIQAQ%253D%253D"
            encoded_q = urllib.parse.quote(clean_q)
            url = f"https://www.youtube.com/results?search_query={encoded_q}&sp={sp_filter}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            req = urllib.request.Request(url, headers=headers)
            html = urllib.request.urlopen(req, timeout=6).read().decode("utf-8")
            
            # Extract videoRenderer items (the actual top-ranked search results, not ads/shorts)
            vids = re.findall(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"', html)
            if not vids:
                matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                seen = set()
                vids = [m for m in matches if not (m in seen or seen.add(m))]
            
            if vids:
                webbrowser.open(f"https://www.youtube.com/watch?v={vids[0]}")
            else:
                searchYoutube(clean_q)
        except Exception as e:
            print(f"[YouTube Error]: {e}")
            searchYoutube(clean_q)
        if spoken:
            speak(spoken)

    elif tool in ("get_time", "get_date"):
        if spoken:
            speak(spoken)
        else:
            sentence_gen = get_ai_response_stream(query, interruption_event=interruption_event)
            spoken = speak_stream(sentence_gen, interruption_event=interruption_event)

    elif tool == "get_weather":
        city = params.get("city", "").strip()
        result = weather_command(city if city else query)
        if result:
            spoken = result
            speak(spoken)
        elif spoken:
            speak(spoken)

    elif tool == "open_app":
        app_name = params.get("name", "").lower().strip()
        if app_name:
            ok = open_windows_app(app_name)
            if ok:
                try:
                    from ai import update_active_state
                    update_active_state("active_app", {"name": app_name})
                except Exception:
                    pass
                if spoken:
                    speak(spoken)
            else:
                fallback_msg = f"I couldn't find {app_name} installed on your device."
                speak(fallback_msg)
                spoken = fallback_msg
        elif spoken:
            speak(spoken)

    elif tool == "open_folder":
        folder_name = params.get("name", "downloads")
        ok, msg = open_folder(folder_name)
        if msg:
            spoken = msg
            speak(spoken)
        elif spoken:
            speak(spoken)

    elif tool == "find_file":
        q = params.get("query", query)
        matches, summary = find_files(q)
        if summary:
            spoken = summary
            speak(spoken)
        elif spoken:
            speak(spoken)
        if matches:
            open_file_or_location(matches[0])

    elif tool in ("ask_about_screen", "read_screen", "take_screenshot"):
        if tool == "take_screenshot":
            capture_screen_image()
            spoken = "Screenshot captured."
            speak(spoken)
        else:
            try:
                question = params.get("question", query)
                from screen_vision import answer_screen_question
                explanation = answer_screen_question(question)
                speak(explanation)
                spoken = explanation
            except Exception as e:
                print("[Screen Vision Error]:", e)
                spoken = "I had trouble reading the screen content."
                speak(spoken)

    elif tool == "type_text":
        app_to_open = params.get("app", "").strip()
        if app_to_open:
            open_windows_app(app_to_open)
            time.sleep(1.0)
        text_to_type = params.get("text", "").strip()
        if text_to_type:
            os_automation.type_text(text_to_type)
        if spoken:
            speak(spoken)

    elif tool == "press_key":
        keys_to_press = params.get("keys", "")
        if keys_to_press:
            os_automation.press_shortcut(keys_to_press)
        if spoken:
            speak(spoken)

    elif tool == "search_and_type":
        text_to_type = params.get("text", "")
        if text_to_type:
            os_automation.search_and_type(text_to_type)
        if spoken:
            speak(spoken)

    elif tool == "scroll_down":
        os_automation.scroll_down()
        if spoken:
            speak(spoken)

    elif tool == "scroll_up":
        os_automation.scroll_up()
        if spoken:
            speak(spoken)

    elif tool == "new_tab":
        url = params.get("url", "")
        os_automation.new_tab(url)
        if spoken:
            speak(spoken)

    elif tool == "close_tab":
        os_automation.close_tab()
        if spoken:
            speak(spoken)

    elif tool == "next_tab":
        os_automation.next_tab()
        if spoken:
            speak(spoken)

    elif tool == "prev_tab":
        os_automation.prev_tab()
        if spoken:
            speak(spoken)

    elif tool == "window_management":
        action_type = params.get("action", "")
        if action_type:
            os_automation.window_action(action_type)
        if spoken:
            speak(spoken)

    elif tool == "lock_pc":
        os_automation.lock_pc()
        if spoken:
            speak(spoken)

    elif tool == "sleep_pc":
        os_automation.sleep_pc()
        if spoken:
            speak(spoken)

    elif tool == "empty_recycle_bin":
        os_automation.empty_recycle_bin()
        if spoken:
            speak(spoken)

    elif tool == "restart_pc":
        os_automation.restart_pc(30)
        if spoken:
            speak(spoken)

    elif tool == "cancel_shutdown":
        os_automation.cancel_shutdown()
        if spoken:
            speak(spoken)

    elif tool == "wikipedia":
        target = params.get("query", query)
        try:
            results = wikipedia.summary(target, sentences=2)
            spoken = results
            speak(spoken)
        except Exception:
            spoken = f"I couldn't find a Wikipedia page for {target}."
            speak(spoken)

    elif tool == "calculate":
        expr = params.get("expression", "").strip()
        if expr:
            Calc(expr, speak)
        elif spoken:
            speak(spoken)

    elif tool == "volume_up":
        os_automation.volume_up()
        if spoken:
            speak(spoken)

    elif tool == "volume_down":
        os_automation.volume_down()
        if spoken:
            speak(spoken)

    elif tool == "mute":
        os_automation.mute()
        if spoken:
            speak(spoken)

    elif tool in ("pause_media", "play_media"):
        os_automation.play_pause_media()
        if spoken:
            speak(spoken)

    elif tool == "next_track":
        os_automation.next_track()
        if spoken:
            speak(spoken)

    elif tool == "prev_track":
        os_automation.prev_track()
        if spoken:
            speak(spoken)

    elif tool in ("system_status", "hardware_metrics"):
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        battery = psutil.sensors_battery()
        bat_status = f"{battery.percent} percent" if battery else "unknown"
        spoken = f"CPU is at {cpu} percent, RAM usage is {ram} percent, and Battery is at {bat_status}."
        speak(spoken)

    elif tool == "set_brightness":
        level = params.get("level", "50")
        try:
            level_int = int(level)
            if sbc:
                sbc.set_brightness(level_int)
                spoken = f"Screen brightness set to {level_int} percent."
                speak(spoken)
        except Exception as e:
            print("Brightness error:", e)
            if spoken:
                speak(spoken)

    elif tool == "open_settings":
        from settings_resolver import open_setting
        setting = params.get("setting", "")
        spoken = open_setting(setting)
        speak(spoken)

    elif tool == "set_volume":
        level = params.get("level", "50")
        try:
            level_int = int(level)
            if AudioUtilities:
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                volume.SetMasterVolumeLevelScalar(level_int / 100.0, None)
                spoken = f"Volume set to {level_int} percent."
                speak(spoken)
        except Exception as e:
            print("Volume error:", e)
            if spoken:
                speak(spoken)

    elif tool == "set_timer":
        res = handle_set_timer(params, query)
        speak(res)
        spoken = res

    elif tool == "set_reminder":
        res = handle_set_reminder(params, query)
        speak(res)
        spoken = res

    elif tool == "list_reminders":
        res = handle_list_reminders()
        speak(res)
        spoken = res

    elif tool == "cancel_reminder":
        res = handle_cancel_reminder(params)
        speak(res)
        spoken = res

    elif tool == "exit":
        spoken = "Goodbye!"
        speak(spoken)
        exit(0)

    else:
        # tool == "chat" or unknown conversational query
        if spoken:
            speak(spoken)
        else:
            sentence_gen = get_ai_response_stream(query, interruption_event=interruption_event)
            spoken = speak_stream(sentence_gen, interruption_event=interruption_event)

    return spoken


def process_agent_query(query):
    """
    Fully agentic query processor powered by Qwen 2.5 3B Instruct.
    Supports multi-action: a single voice command can trigger multiple tools
    executed in sequence (e.g. "open chrome and play some music").
    """
    # Load conversation history so the LLM has full context for follow-up commands
    memory = load_memory()
    history = memory.get("conversations", [])
    history = history[-15:]

    # Snapshot clipboard state before asking the LLM (for Mirror Memory tracking)
    clipboard_used = bool(get_clipboard_text())

    # Probe guard — if user is asking adversarial/introspective questions
    # that slipped through the intent layer, force to conversational chat.
    if _RE_PROBE_GUARD.search(query):
        actions = [{"tool": "chat", "params": {}, "speak": "", "remember": ""}]
    else:
        actions = get_agent_action(query, conversation_history=history)

    combined_spoken = []
    last_tool = "chat"
    last_remember = ""
    interruption_event = threading.Event()

    for action in actions:
        if interruption_event.is_set():
            break
        spoken = _execute_action(action, query, combined_spoken, interruption_event=interruption_event)
        if spoken:
            combined_spoken.append(spoken)
        last_tool = action.get("tool", "chat")
        if action.get("remember"):
            last_remember = action["remember"]
        if interruption_event.is_set():
            break

    # Save interaction to memory once for the whole multi-action turn
    final_reply = " ".join(combined_spoken).strip()
    if not final_reply and actions:
        action_desc = []
        for act in actions:
            t = act.get("tool", "action")
            p = act.get("params", {})
            if t == "play_youtube":
                q_song = p.get("query", "")
                action_desc.append(f"Playing {q_song} on YouTube." if q_song else "Playing music on YouTube.")
            elif t == "open_app":
                action_desc.append(f"Opened {p.get('name', 'application')}.")
            elif t == "web_search":
                action_desc.append(f"Searched for {p.get('query', '')}.")
            else:
                action_desc.append(f"Completed {t.replace('_', ' ')}.")
        final_reply = " ".join(action_desc)

    add_to_memory(
        query,
        final_reply or f"Completed {last_tool.replace('_', ' ')}",
        tool=last_tool,
        clipboard_used=clipboard_used,
        remember=last_remember,
    )



if __name__ == "__main__":
    _model_info = get_active_model_info()
    print("==========================================", flush=True)
    print(f"   AMIGO VOICE ASSISTANT - {_model_info['name'].upper()}", flush=True)
    print("   [ AGENTIC AI MODE ENABLED ]            ", flush=True)
    print("==========================================", flush=True)
    print(flush=True)
    print("  Choose input mode:", flush=True)
    print("  [1] Voice (microphone) - needs internet", flush=True)
    print("  [2] Type  (keyboard)   - works offline", flush=True)
    print("  [3] Both  (voice + type fallback)", flush=True)
    print(flush=True)

    mode = input("  Enter 1, 2, or 3 (default=3): ").strip()
    if mode not in ("1", "2", "3"):
        mode = "3"
    _ACTIVE_MODE = mode

    mode_names = {"1": "Voice", "2": "Type", "3": "Voice + Type fallback"}
    print(f"\n  -> {mode_names[mode]} mode selected.\n", flush=True)

    print(f"[ AMIGO AI ] Loading Local AI Engine ({_model_info['name']})...", flush=True)
    init_local_llm()
    print("[ AMIGO AI ] Local AI Engine ready.\n", flush=True)

    # Kick off the unified system index build in the background.
    # Apps + file search sources are indexed once and shared across all modules.
    ensure_built()

    # Initialize Reminders & Timers background scheduler
    init_reminders(speak_callback=speak)

    speak("Amigo Voice Assistant activated.")

    while True:
        try:
            query = None

            if mode == "1":
                # Voice only
                query = take_command()
                if query == "OFFLINE_ERROR":
                    query = input("\n[ OFFLINE MODE ] Type your command: ").strip()

            elif mode == "2":
                # Type only
                query = input("You: ").strip()

            elif mode == "3":
                # Voice first, type fallback
                query = take_command()
                if query == "OFFLINE_ERROR":
                    query = input("\n[ OFFLINE MODE ] Type your command: ").strip()
                elif not query or query.lower() == "none":
                    query = input("Voice didn't catch that. Type here: ").strip()

            if not query or query.lower() in ("none", "offline_error"):
                continue

            process_agent_query(query)

        except KeyboardInterrupt:
            print("\nExiting Amigo Voice Assistant.")
            speak("Goodbye!")
            break
        except Exception as e:
            print(f"[Error]: {e}")
            time.sleep(1)
