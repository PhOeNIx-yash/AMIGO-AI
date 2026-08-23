"""
UI Web Dashboard Server & Background Voice Engine for Amigo Assistant.
TTS  : Kokoro ONNX (fast neural) -> win32com SAPI -> pyttsx3 fallback
LLM  : get_agent_action from local_llm (Qwen 2.5 3B Instruct agentic tool dispatch)
"""

import sys
import logging

# Configure logging FIRST so messages from all imports are visible
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,   # stdout so it can't be accidentally redirected
    force=True,
)
print("[ AMIGO UI SERVER ] Starting up...", flush=True)

import atexit
import datetime
import json
import os
import queue
import re
import signal
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import psutil
import pyautogui
import wikipedia
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

pyautogui.FAILSAFE = False

import speech_recognition as sr

import os_automation
from ai import (
    add_to_memory,
    get_ai_response,
    update_active_state,
    load_memory,
    save_memory,
)
from app_opener import (
    find_files,
    open_file_or_location,
    open_folder,
    open_windows_app,
)
from Calculatenumbers import Calc
from local_llm import (
    get_agent_action,
    get_clipboard_text,
    set_active_model,
)
from Searchnow import searchGoogle, searchYoutube, scrape_web_info
from weather import weather_command, get_weather_data
from reminder_timer import (
    init_reminders,
    handle_set_timer,
    handle_set_reminder,
    handle_list_reminders,
    handle_cancel_reminder,
)

print("[ AMIGO UI SERVER ] All imports loaded.", flush=True)

logger = logging.getLogger("amigo.ui_server")

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")


# ---------------------------------------------------------------------------
# SSE Event Broadcaster
# ---------------------------------------------------------------------------
class EventBroadcaster:
    def __init__(self):
        self.listeners = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=50)
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def broadcast(self, event_type, data=None):
        payload = {"type": event_type}
        if data:
            payload.update(data)
        msg = "data: " + json.dumps(payload) + "\n\n"
        with self.lock:
            for q in list(self.listeners):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


broadcaster = EventBroadcaster()
current_state = "idle"

_current_media = {
    "title": "No music playing",
    "artist": "Amigo Media Player",
    "thumbnail": "",
    "video_id": "",
    "url": "",
    "status": "idle",
    "volume": 75,
}


def set_assistant_state(state_name: str) -> None:
    global current_state
    current_state = state_name
    broadcaster.broadcast("state_change", {"state": state_name})


# ---------------------------------------------------------------------------
# TTS Engine — Kokoro ONNX -> win32com SAPI -> pyttsx3 (mirrors amigo main.py)
# ---------------------------------------------------------------------------
_kokoro_instance = None
_USE_KOKORO = False
_USE_WIN32 = False
_tts_engine = None

_UI_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "kokoro-onnx"
)
_UI_MODEL_PATH = os.path.join(_UI_MODEL_DIR, "kokoro-v1.0.onnx")
_UI_VOICES_PATH = os.path.join(_UI_MODEL_DIR, "voices-v1.0.bin")

try:
    import sounddevice as _sd
    from kokoro_onnx import Kokoro as _KokoroOnnx

    _USE_KOKORO = True
    logger.info("[TTS] Kokoro ONNX available.")
except ImportError as _e:
    logger.info("[TTS] kokoro-onnx not available, trying win32com SAPI …")
    try:
        import win32com.client as _win32

        _sapi = _win32.Dispatch("SAPI.SpVoice")
        _sapi.Rate = 1
        _USE_WIN32 = True
        logger.info("[TTS] Using win32com SAPI SpVoice.")
    except Exception as _e2:
        logger.info("[TTS] win32com not available, falling back to pyttsx3")
        try:
            import pyttsx3 as _pyttsx3
        except Exception:
            pass

KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.15
KOKORO_LANG  = "en-us"
_tts_lock = threading.Lock()


def _get_kokoro():
    """Lazy-load Kokoro ONNX model."""
    global _kokoro_instance
    if _kokoro_instance is None:
        if not os.path.exists(_UI_MODEL_PATH):
            raise FileNotFoundError("Kokoro ONNX model not found at " + _UI_MODEL_PATH)
        logger.info("[TTS] Loading Kokoro ONNX model …")
        _kokoro_instance = _KokoroOnnx(_UI_MODEL_PATH, _UI_VOICES_PATH)
        logger.info("[TTS] Kokoro ONNX ready.")
    return _kokoro_instance


# Prewarm Kokoro ONNX model in background on startup
def _warmup_kokoro():
    if _USE_KOKORO:
        try:
            _get_kokoro()
        except Exception as e:
            logger.debug(f"[TTS Warmup] {e}")

threading.Thread(target=_warmup_kokoro, daemon=True).start()

# Dedicated asynchronous speech worker queue for zero UI delay
_speech_queue = queue.Queue()


def _trim_audio_silence(samples, threshold=0.008, pad_ms=100, sr=24000):
    """Trim excess trailing silence from sentence chunks to maintain natural conversational pacing."""
    if samples is None or len(samples) == 0:
        return samples
    import numpy as np
    mask = np.abs(samples) > threshold
    if not np.any(mask):
        return samples
    last_idx = int(np.max(np.where(mask)[0]))
    pad_samples = int((pad_ms / 1000.0) * sr)
    end_idx = min(len(samples), last_idx + pad_samples)
    return samples[:end_idx]


def _synthesize_and_play_kokoro(kokoro, text):
    """Seamless double-buffered streaming synthesis: pre-synthesizes chunk N+1 while chunk N plays."""
    clean_text = text.strip()
    if not clean_text:
        return

    # Split long text into natural sentence chunks for streaming
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', clean_text) if s.strip()]
    if len(sentences) <= 1:
        samples, sample_rate = kokoro.create(
            clean_text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG
        )
        if samples is not None and len(samples) > 0:
            _sd.play(samples, samplerate=sample_rate)
            _sd.wait()
        return

    audio_queue = queue.Queue(maxsize=3)
    sentinel = object()

    def producer():
        try:
            for s in sentences:
                if not s:
                    continue
                samp, sr = kokoro.create(s, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG)
                if samp is not None and len(samp) > 0:
                    samp = _trim_audio_silence(samp, sr=sr)
                    audio_queue.put((samp, sr))
        except Exception as e:
            logger.error(f"[TTS Stream Error] {e}")
        finally:
            audio_queue.put(sentinel)

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    while True:
        item = audio_queue.get()
        if item is sentinel:
            break
        samples, sample_rate = item
        _sd.play(samples, samplerate=sample_rate)
        _sd.wait()


def _speech_worker():
    while True:
        text = _speech_queue.get()
        if not text:
            _speech_queue.task_done()
            continue
        try:
            set_assistant_state("speaking")
            broadcaster.broadcast("chat_message", {"sender": "assistant", "text": text})

            if _USE_KOKORO:
                try:
                    kokoro = _get_kokoro()
                    _synthesize_and_play_kokoro(kokoro, text)
                    continue
                except Exception as e:
                    logger.error("[TTS] Kokoro error: " + str(e) + " — falling back")

            if _USE_WIN32:
                try:
                    _sapi.Speak(text)
                    continue
                except Exception as e:
                    logger.error("[TTS] SAPI error: " + str(e))

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
                logger.error("[TTS] pyttsx3 error: " + str(e))
                _tts_engine = None
        finally:
            set_assistant_state("idle")
            _speech_queue.task_done()


threading.Thread(target=_speech_worker, daemon=True).start()


def speak_ui(text: str, block: bool = False) -> None:
    """Speak text asynchronously, broadcast SSE state, and animate UI with 0ms delay."""
    if not text:
        return
    if block:
        _speech_queue.put(text)
        _speech_queue.join()
    else:
        _speech_queue.put(text)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def extract_and_open_urls(text: str) -> bool:
    full_urls = re.findall(
        r'https?://[^\s<>"{}|\\^`\[\]]*[^\s<>"{}|\\^`\[\].,;:!?]', text
    )
    bare_domains = re.findall(
        r"\b([a-zA-Z0-9-]+\.(?:com|org|net|gov|edu|io|co\.uk|in|info))\b", text
    )
    opened = False
    seen: set = set()
    for url in full_urls[:2]:
        if url not in seen:
            webbrowser.open(url)
            seen.add(url)
            opened = True
    if not opened:
        for domain in bare_domains[:2]:
            full = "https://" + domain
            if full not in seen:
                webbrowser.open(full)
                seen.add(full)
                opened = True
    return opened


# ---------------------------------------------------------------------------
# Microphone helpers
# ---------------------------------------------------------------------------
_recognizer = sr.Recognizer()
_recognizer.energy_threshold = 400
_recognizer.dynamic_energy_threshold = True
_recognizer.pause_threshold = 1.0
_recognizer.phrase_threshold = 0.3
_mic_calibrated = False


def _calibrate_mic_once():
    """Calibrate ambient noise once at first use (not every listen cycle)."""
    global _mic_calibrated
    if _mic_calibrated:
        return
    try:
        with sr.Microphone() as source:
            logger.info("Calibrating microphone (one-time)…")
            _recognizer.adjust_for_ambient_noise(source, duration=0.3)
            _mic_calibrated = True
            logger.info("Microphone calibrated.")
    except Exception as e:
        logger.debug(f"Mic calibration note: {e}")


def take_command_ui(max_retries: int = 3) -> str:
    """Listen to mic and return recognized speech lower-cased."""
    _calibrate_mic_once()
    for attempt in range(max_retries):
        try:
            with sr.Microphone() as source:
                audio = _recognizer.listen(source, timeout=8, phrase_time_limit=15)
                query = _recognizer.recognize_google(audio, language="en-US")
                logger.info("[Voice Recognized]: " + query)
                return query.lower()
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
            return "None"
    return "None"


# ---------------------------------------------------------------------------
# Tool Handlers for UI Server
# ---------------------------------------------------------------------------

def _ui_web_search(params, query, spoken):
    q = params.get("query", query).strip() or query
    try:
        update_active_state("last_search", {"query": q})
    except Exception:
        pass
    snippets = scrape_web_info(q)
    if snippets:
        response = get_ai_response(query, web_context=snippets)
        speak_ui(response)
        if any(kw in query.lower() for kw in ("google", "browser", "open google", "search google", "show in browser")):
            searchGoogle(q)
        return response, "https://www.google.com/search?q=" + urllib.parse.quote(q)
    else:
        searchGoogle(q)
        if not spoken:
            spoken = get_ai_response(query)
        speak_ui(spoken)
        return spoken, "https://www.google.com/search?q=" + urllib.parse.quote(q)

def _ui_open_website(params, query, spoken):
    raw_url = params.get("url", "")
    url = None
    if raw_url:
        url = raw_url if raw_url.startswith("http") else "https://" + raw_url
        webbrowser.open(url)
        try:
            update_active_state("active_subject", {"name": url, "category": "website"})
        except Exception:
            pass
    else:
        searchGoogle(query)
    return spoken, url

def _ui_play_youtube(params, query, spoken):
    q = params.get("query", query)

    def _play():
        global _current_media
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
            search_url = (
                f"https://www.youtube.com/results?search_query={encoded_q}&sp={sp_filter}"
            )
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            }
            req = urllib.request.Request(search_url, headers=headers)
            html = urllib.request.urlopen(req, timeout=6).read().decode("utf-8")
            
            # Extract top videoRenderer videoId
            vids = re.findall(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"', html)
            if not vids:
                matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                seen_ids: set = set()
                vids = [m for m in matches if not (m in seen_ids or seen_ids.add(m))]
            
            vid = vids[0] if vids else None
            
            # Extract video title
            title = q.title()
            title_m = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"', html)
            if not title_m:
                title_m = re.search(r'"title":\{"accessibility":\{"accessibilityData":\{"label":"([^"]+)"', html)
            if title_m:
                title = title_m.group(1).replace(r"\u0026", "&").replace(r'\"', '"')
            
            # Extract artist/channel
            channel = "YouTube Music"
            channel_m = re.search(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)
            if not channel_m:
                channel_m = re.search(r'"longBylineText":\{"runs":\[\{"text":"([^"]+)"', html)
            if channel_m:
                channel = channel_m.group(1).replace(r"\u0026", "&")
            
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else "https://images.unsplash.com/photo-1614680376593-902f749f7ffc?w=400&q=80"
            play_url = f"https://www.youtube.com/watch?v={vid}" if vid else f"https://www.youtube.com/results?search_query={encoded_q}"
            
            _current_media = {
                "title": title,
                "artist": channel,
                "thumbnail": thumb,
                "video_id": vid or "",
                "url": play_url,
                "status": "playing",
                "volume": _current_media.get("volume", 75),
            }
            broadcaster.broadcast("media_update", _current_media)
            
            try:
                update_active_state("current_media", {
                    "title": title,
                    "artist": channel,
                    "platform": "YouTube",
                    "query": q,
                })
            except Exception:
                pass

            if vid:
                webbrowser.open(play_url)
            else:
                searchYoutube(q)
        except Exception as e:
            logger.error(f"[YouTube Error]: {e}")
            searchYoutube(q)

    threading.Thread(target=_play, daemon=True).start()
    return spoken, None

def get_active_model_info():
    return {
        "key": "qwen2.5_3b",
        "name": "Qwen 2.5 3B Instruct",
        "type": "local_gguf",
        "context_length": 8192,
        "tts_engine": "Kokoro ONNX (Neural)" if _USE_KOKORO else "Windows Native SAPI",
    }


def get_available_models():
    return [
        {"key": "qwen2.5_3b", "name": "Qwen 2.5 3B Instruct", "status": "active", "type": "local"},
        {"key": "llama3.2_3b", "name": "Llama 3.2 3B Instruct", "status": "available", "type": "local"},
    ]


def _ui_get_time(params, query, spoken):
    current_t = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
    return f"It is currently {current_t}.", None


def _ui_get_date(params, query, spoken):
    current_d = datetime.datetime.now().strftime("%A, %B %d, %Y")
    return f"Today is {current_d}.", None

def _ui_get_weather(params, query, spoken):
    city = params.get("city", "").strip()
    result = weather_command(city if city else query)
    return result or spoken, None

def _ui_open_app(params, query, spoken):
    app_name = params.get("name", "").lower().strip()
    if app_name:
        ok = open_windows_app(app_name)
        if ok:
            try:
                update_active_state("active_app", {"name": app_name})
            except Exception:
                pass
            return spoken or f"Opening {app_name}.", None
        else:
            return f"I couldn't find {app_name} installed on your device.", None
    return spoken, None

def _ui_open_folder(params, query, spoken):
    folder_name = params.get("name", "downloads")
    ok, msg = open_folder(folder_name)
    return msg if msg else spoken, None

def _ui_find_file(params, query, spoken):
    q = params.get("query", query)
    matches, summary = find_files(q)
    if matches:
        open_file_or_location(matches[0])
    return summary if summary else spoken, None

def _ui_take_screenshot(params, query, spoken):
    try:
        from screen_vision import capture_screen_image
        capture_screen_image("amigo_screenshot.png")
        return "Screenshot saved.", None
    except Exception as e:
        logger.error("[Screenshot] Error: " + str(e))
    return spoken, None

def _ui_type_text(params, query, spoken):
    app_to_open = params.get("app", "").strip()
    if app_to_open:
        open_windows_app(app_to_open)
        time.sleep(1.0)
    text_to_type = params.get("text", "").strip()
    if text_to_type:
        os_automation.type_text(text_to_type)
    return spoken, None

def _ui_press_key(params, query, spoken):
    keys_to_press = params.get("keys", "")
    if keys_to_press:
        os_automation.press_shortcut(keys_to_press)
    return spoken, None

def _ui_window_management(params, query, spoken):
    action_type = params.get("action", "")
    if action_type:
        os_automation.window_action(action_type)
    return spoken, None

def _ui_wikipedia(params, query, spoken):
    target = params.get("query", query)
    try:
        results = wikipedia.summary(target, sentences=2)
        return results, None
    except Exception as e:
        logger.debug(f"[Wikipedia Error]: {e}")
        return f"I couldn't find a Wikipedia page for {target}.", None

def _ui_calculate(params, query, spoken):
    expr = params.get("expression", query).strip()
    if expr:
        res = Calc(expr)
        if res:
            return f"The answer is {res}.", None
    return spoken or "Calculation completed.", None

def _ui_volume_up(params, query, spoken):
    os_automation.volume_up()
    return spoken or "Volume increased.", None

def _ui_volume_down(params, query, spoken):
    os_automation.volume_down()
    return spoken or "Volume decreased.", None

def _ui_mute(params, query, spoken):
    os_automation.mute()
    return spoken or "Audio muted.", None

def _ui_pause_media(params, query, spoken):
    global _current_media
    os_automation.play_pause_media()
    _current_media["status"] = "paused"
    broadcaster.broadcast("media_update", _current_media)
    return spoken or "Media paused.", None

def _ui_play_media(params, query, spoken):
    global _current_media
    os_automation.play_pause_media()
    _current_media["status"] = "playing"
    broadcaster.broadcast("media_update", _current_media)
    return spoken or "Media resumed.", None

def _ui_next_track(params, query, spoken):
    os_automation.next_track()
    return spoken or "Next track.", None

def _ui_prev_track(params, query, spoken):
    os_automation.prev_track()
    return spoken or "Previous track.", None

def _ui_system_status(params, query, spoken):
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        battery = psutil.sensors_battery()
        bat_status = f"{battery.percent:.0f} percent" if battery else "unknown"
        status_msg = (
            f"CPU is at {cpu} percent, RAM usage is {ram} percent, "
            f"and Battery is at {bat_status}."
        )
        return status_msg, None
    except Exception as e:
        logger.error("[System Status] Error: " + str(e))
    return spoken, None

def _ui_search_and_type(params, query, spoken):
    text_to_type = params.get("text", "")
    if text_to_type:
        os_automation.search_and_type(text_to_type)
    return spoken, None

def _ui_scroll_down(params, query, spoken):
    os_automation.scroll_down()
    return spoken, None

def _ui_scroll_up(params, query, spoken):
    os_automation.scroll_up()
    return spoken, None

def _ui_new_tab(params, query, spoken):
    url = params.get("url", "")
    os_automation.new_tab(url)
    return spoken, None

def _ui_close_tab(params, query, spoken):
    os_automation.close_tab()
    return spoken, None

def _ui_next_tab(params, query, spoken):
    os_automation.next_tab()
    return spoken, None

def _ui_prev_tab(params, query, spoken):
    os_automation.prev_tab()
    return spoken, None

def _ui_read_screen(params, query, spoken):
    try:
        from screen_vision import answer_screen_question
        question = params.get("question", query)
        explanation = answer_screen_question(question)
        return explanation, None
    except Exception as e:
        logger.error("[Read Screen] Error: " + str(e))
        return "Could not read the screen.", None

def _ui_lock_pc(params, query, spoken):
    os_automation.lock_pc()
    return spoken, None

def _ui_sleep_pc(params, query, spoken):
    os_automation.sleep_pc()
    return spoken, None

def _ui_empty_recycle_bin(params, query, spoken):
    os_automation.empty_recycle_bin()
    return spoken or "Recycle bin emptied.", None

def _ui_restart_pc(params, query, spoken):
    os_automation.restart_pc(30)
    return spoken or "Restarting in 30 seconds.", None

def _ui_cancel_shutdown(params, query, spoken):
    os_automation.cancel_shutdown()
    return spoken or "Shutdown cancelled.", None

def _ui_exit(params, query, spoken):
    return spoken or "Goodbye!", None

def _ui_set_brightness(params, query, spoken):
    level = params.get("level", "50")
    try:
        level_int = max(0, min(100, int(level)))
        success = False
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(level_int)
            success = True
        except Exception as e:
            logger.debug(f"[Brightness SBC fallback] {e}")

        if not success:
            import subprocess
            ps_cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {level_int})"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=4)

        return f"Screen brightness set to {level_int} percent.", None
    except Exception as e:
        logger.error(f"[Brightness Error] {e}")
        return f"Could not adjust brightness to {level}.", None

def _ui_open_settings(params, query, spoken):
    from settings_resolver import open_setting
    msg = open_setting(params.get("setting", ""))
    return msg, None

def _ui_set_volume(params, query, spoken):
    level = params.get("level", "50")
    try:
        import pythoncom
        from pycaw.pycaw import AudioUtilities
        pythoncom.CoInitialize()
        try:
            level_int = max(0, min(100, int(level)))
            devices = AudioUtilities.GetSpeakers()
            if hasattr(devices, "EndpointVolume"):
                devices.EndpointVolume.SetMasterVolumeLevelScalar(level_int / 100.0, None)
            else:
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                volume.SetMasterVolumeLevelScalar(level_int / 100.0, None)
            return f"Volume set to {level_int} percent.", None
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        logger.error(f"[Volume Error] {e}")
    return spoken or f"Volume adjusted to {level} percent.", None

def _ui_set_timer(params, query, spoken):
    res = handle_set_timer(params, query)
    return res, None

def _ui_set_reminder(params, query, spoken):
    res = handle_set_reminder(params, query)
    return res, None

def _ui_list_reminders(params, query, spoken):
    res = handle_list_reminders()
    return res, None

def _ui_cancel_reminder(params, query, spoken):
    res = handle_cancel_reminder(params)
    return res, None

def _ui_chat(params, query, spoken):
    response = get_ai_response(query)
    extract_and_open_urls(response)
    return response, None


# Dispatch table: tool name → UI handler
UI_TOOL_HANDLERS = {
    "web_search":        _ui_web_search,
    "open_website":      _ui_open_website,
    "play_youtube":      _ui_play_youtube,
    "get_time":          _ui_get_time,
    "get_date":          _ui_get_date,
    "get_weather":       _ui_get_weather,
    "open_app":          _ui_open_app,
    "take_screenshot":   _ui_take_screenshot,
    "read_screen":       _ui_read_screen,
    "ask_about_screen":  _ui_read_screen,
    "type_text":         _ui_type_text,
    "search_and_type":   _ui_search_and_type,
    "scroll_down":       _ui_scroll_down,
    "scroll_up":         _ui_scroll_up,
    "new_tab":           _ui_new_tab,
    "close_tab":         _ui_close_tab,
    "next_tab":          _ui_next_tab,
    "prev_tab":          _ui_prev_tab,
    "press_key":         _ui_press_key,
    "window_management": _ui_window_management,
    "lock_pc":           _ui_lock_pc,
    "sleep_pc":          _ui_sleep_pc,
    "empty_recycle_bin": _ui_empty_recycle_bin,
    "restart_pc":        _ui_restart_pc,
    "cancel_shutdown":   _ui_cancel_shutdown,
    "wikipedia":         _ui_wikipedia,
    "calculate":         _ui_calculate,
    "volume_up":         _ui_volume_up,
    "volume_down":       _ui_volume_down,
    "mute":              _ui_mute,
    "pause_media":       _ui_pause_media,
    "play_media":        _ui_play_media,
    "next_track":        _ui_next_track,
    "prev_track":        _ui_prev_track,
    "system_status":     _ui_system_status,
    "hardware_metrics":  _ui_system_status,
    "set_brightness":    _ui_set_brightness,
    "open_settings":     _ui_open_settings,
    "set_timer":         _ui_set_timer,
    "stopwatch":         _ui_set_timer,
    "set_reminder":      _ui_set_reminder,
    "list_reminders":    _ui_list_reminders,
    "cancel_reminder":   _ui_cancel_reminder,
    "open_folder":       _ui_open_folder,
    "find_file":         _ui_find_file,
    "set_volume":        _ui_set_volume,
    "exit":              _ui_exit,
    "chat":              _ui_chat,
}


# ---------------------------------------------------------------------------
# Core Query Processor (agentic)
# ---------------------------------------------------------------------------
def process_query(query: str, is_voice: bool = True) -> dict:
    """
    Fully agentic query processor powered by Qwen 2.5 3B Instruct.
    Supports multi-action execution and broadcasts SSE events to the web UI.
    """
    set_assistant_state("processing")
    memory = load_memory()
    history = memory.get("conversations", [])
    history = history[-15:]  # Full 15-turn context window

    # Snapshot clipboard state before LLM call (for Mirror Memory tracking)
    clipboard_used = bool(get_clipboard_text())

    actions = get_agent_action(query, conversation_history=history)

    combined_spoken = []
    last_tool = "chat"
    last_params = {}
    last_remember = ""
    last_url = None

    for action in actions:
        tool     = action.get("tool", "chat")
        params   = action.get("params", {})
        spoken   = action.get("speak", "")
        remember = action.get("remember", "")

        last_tool = tool
        last_params = params
        if remember:
            last_remember = remember

        # chat & web_search tools: clear spoken so handler synthesizes response with full memory/web context
        if tool in ("chat", "web_search"):
            spoken = ""

        logger.info(f"[Query Action] tool={tool!r} params={params}")
        broadcaster.broadcast("intent_detected", {"intent": tool.upper(), "params": params})

        # Dispatch to handler — handlers return (result_text, url)
        handler = UI_TOOL_HANDLERS.get(tool, _ui_chat)
        res_spoken, res_url = handler(params, query, spoken)

        # Single authoritative sound output: speak exactly ONCE per action
        final_to_speak = res_spoken if res_spoken else spoken
        if final_to_speak:
            speak_ui(final_to_speak)
            combined_spoken.append(final_to_speak)

        if res_url:
            last_url = res_url

    # Save to memory — updates all 3 memory layers
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

    return {"tool": last_tool, "params": last_params, "response": final_reply, "url": last_url}


# ---------------------------------------------------------------------------
# Background Voice Loop
# ---------------------------------------------------------------------------
_running = True


def background_voice_loop():
    """Background daemon: wake-word detection + command processing."""
    global _running
    logger.info("[Voice Loop] Background listening engine started.")
    wake_phrases = ["hey amigo", "hi amigo", "hello amigo", "amigo"]
    while _running:
        try:
            set_assistant_state("idle")
            query = take_command_ui()
            if not _running:
                break
            if not query or query.lower() == "none":
                continue
            cleaned = query
            for phrase in wake_phrases:
                cleaned = cleaned.replace(phrase, "").strip()
            if cleaned:
                process_query(cleaned, is_voice=True)
            else:
                speak_ui("Yes, how can I assist you?")
                cmd = take_command_ui()
                if cmd and cmd.lower() != "none" and _running:
                    process_query(cmd, is_voice=True)
        except Exception as e:
            if not _running:
                break
            logger.error("[Voice Loop Exception]: " + str(e))
            time.sleep(1)
    logger.info("[Voice Loop] Background listening engine stopped.")


# ---------------------------------------------------------------------------
# Flask Routes & CORS Bridge for React UI
# ---------------------------------------------------------------------------

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


@app.route("/")
def index():
    dist_index = os.path.join(os.path.dirname(__file__), "ui_app", "dist", "index.html")
    if os.path.exists(dist_index):
        with open(dist_index, "r", encoding="utf-8") as f:
            return f.read()
    info = get_active_model_info()
    return jsonify({
        "status": "online",
        "service": "Amigo AI Engine & Windows 11 Voice Assistant Backend",
        "model": info.get("name", "Qwen 2.5 3B Instruct"),
        "endpoints": {
            "assistant_process": "/api/assistant/process",
            "action_execute": "/api/action/execute",
            "status": "/api/status",
            "system_stats": "/api/system-stats",
            "history": "/api/history"
        }
    })


@app.route("/assets/<path:path>")
def serve_assets(path):
    dist_assets = os.path.join(os.path.dirname(__file__), "ui_app", "dist", "assets")
    return send_from_directory(dist_assets, path)


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/api/assistant/process", methods=["POST", "OPTIONS"])
def api_assistant_process():
    """Universal bridge for the React Windows 11 Voice Assistant UI."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})

    data = request.get_json() or {}

    # Health check ping
    if data.get("isHealthCheck") or data.get("prompt") == "ping_health_check":
        info = get_active_model_info()
        return jsonify({
            "status": "online",
            "message": "Amigo AI Engine online",
            "model": info.get("name", "Qwen 2.5 3B Instruct"),
            "version": "Windows 11 Voice Assistant",
        })

    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Empty prompt"}), 400

    context = data.get("context") or {}
    broadcaster.broadcast("chat_message", {"sender": "user", "text": prompt})

    # Run query through Amigo's agentic pipeline
    result = process_query(prompt, is_voice=False)
    tool = result.get("tool", "chat")
    params = result.get("params", {})
    response_text = result.get("response", "")
    url = result.get("url", "")

    # Build actionCards based on intent
    action_cards = []
    if tool == "open_app":
        app_name = params.get("name", "Application")
        action_cards.append({
            "id": "act-open-app",
            "type": "general",
            "title": f"Open {app_name.title()}",
            "subtitle": "Windows desktop application",
            "selected": True,
            "badge": "App",
            "payload": {"tool": "open_app", "name": app_name},
        })
    elif tool in ("open_website", "web_search") and url:
        action_cards.append({
            "id": "act-web",
            "type": "general",
            "title": "Open Link",
            "subtitle": url,
            "selected": True,
            "url": url,
            "badge": "Web",
        })
    elif tool == "play_youtube":
        q = params.get("query", prompt)
        action_cards.append({
            "id": "act-yt",
            "type": "general",
            "title": f"Play: {q}",
            "subtitle": "YouTube Media",
            "selected": True,
            "badge": "Media",
            "payload": {"tool": "play_youtube", "query": q},
        })
    elif tool == "get_weather":
        city = params.get("city", "").strip()
        w_data = get_weather_data(city) if city else get_weather_data("")
        resolved_city = w_data.get("city", city.title() if city else "Local Area")
        action_cards.append({
            "id": "act-weather",
            "type": "weather",
            "title": f"Weather in {resolved_city}",
            "subtitle": response_text,
            "selected": True,
            "badge": "Weather",
            "payload": {
                "tool": "get_weather",
                "city": resolved_city,
                "temp_c": w_data.get("temp_c", ""),
                "temp_f": w_data.get("temp_f", ""),
                "feels_like_c": w_data.get("feels_like_c", ""),
                "condition": w_data.get("condition", ""),
                "humidity": w_data.get("humidity", ""),
                "wind_kmph": w_data.get("wind_kmph", ""),
                "uv_index": w_data.get("uv_index", ""),
                "icon_type": w_data.get("icon_type", "sunny"),
            },
        })
    elif tool in ("set_timer", "timer", "stopwatch") or re.search(r"\b(timer|countdown|stopwatch)\b", prompt, re.IGNORECASE):
        from reminder_timer import parse_relative_seconds
        dur_val = params.get("duration") or params.get("seconds") or ""
        parsed_secs = parse_relative_seconds(str(dur_val)) if dur_val else None
        if not parsed_secs:
            parsed_secs = parse_relative_seconds(prompt) or 300

        is_stopwatch = tool == "stopwatch" or "stopwatch" in prompt.lower()
        label = params.get("label") or ("Stopwatch" if is_stopwatch else "Timer")
        
        mins = int(parsed_secs // 60)
        secs = int(parsed_secs % 60)
        subtitle_text = "Live Active Stopwatch" if is_stopwatch else f"{mins}m {secs}s countdown"

        action_cards.append({
            "id": "act-timer",
            "type": "stopwatch" if is_stopwatch else "timer",
            "title": "Stopwatch" if is_stopwatch else f"Timer: {label}",
            "subtitle": subtitle_text,
            "selected": True,
            "badge": "Stopwatch" if is_stopwatch else "Timer",
            "payload": {
                "tool": "stopwatch" if is_stopwatch else "set_timer",
                "duration_seconds": parsed_secs,
                "seconds": parsed_secs,
                "label": label,
                "mode": "stopwatch" if is_stopwatch else "timer",
            },
        })

    formatted_response = {
        "speechReply": response_text or f"Executing {tool.replace('_', ' ')}",
        "displayTitle": prompt,
        "intent": tool,
        "requiresDisambiguation": False,
        "actionCards": action_cards,
        "contactMatches": [],
        "executionSummary": {
            "status": "completed",
            "headline": "Action Completed",
            "details": response_text or f"Completed {tool.replace('_', ' ')}.",
            "secondaryDetails": url if url else "",
        },
    }
    return jsonify(formatted_response)


@app.route("/api/assistant/process/action", methods=["POST", "OPTIONS"])
@app.route("/api/action/execute", methods=["POST", "OPTIONS"])
def api_action_execute():
    """Execute action callback from the React UI."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.get_json() or {}
    payload = data.get("payload") or {}
    tool = payload.get("tool") or data.get("type", "chat")
    handler = UI_TOOL_HANDLERS.get(tool)
    if handler:
        spoken, url = handler(payload, data.get("title", ""), "")
        return jsonify({"success": True, "message": spoken or "Executed", "url": url})
    return jsonify({"success": True, "message": "Action completed"})


@app.route("/api/transcribe", methods=["POST", "OPTIONS"])
def api_transcribe():
    """Audio transcription endpoint bridge."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.get_json() or {}
    return jsonify({"transcription": data.get("text", "")})


@app.route("/events")
def events():
    def stream():
        q = broadcaster.subscribe()
        try:
            yield "data: " + json.dumps(
                {"type": "state_change", "state": current_state}
            ) + "\n\n"
            while True:
                msg = q.get()
                yield msg
        except GeneratorExit:
            broadcaster.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/status")
def get_status():
    return jsonify({"state": current_state})


@app.route("/api/system-stats")
def get_system_stats():
    """Live system stats for the header widget (CPU, RAM, Battery)."""
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        return jsonify({
            "cpu":     round(cpu, 1),
            "ram":     round(mem.percent, 1),
            "battery": round(battery.percent, 1) if battery else None,
            "plugged": battery.power_plugged if battery else None,
        })
    except Exception as e:
        logger.error(f"[System Stats] {e}")
        return jsonify({"cpu": 0, "ram": 0, "battery": None, "plugged": None})


@app.route("/api/history")
def get_history():
    memory = load_memory()
    return jsonify(memory)


@app.route("/api/query", methods=["POST"])
def handle_query():
    data = request.get_json() or {}
    user_query = data.get("query", "").strip()
    if not user_query:
        return jsonify({"error": "Empty query"}), 400
    broadcaster.broadcast("chat_message", {"sender": "user", "text": user_query})
    result = process_query(user_query, is_voice=False)
    return jsonify(result)


@app.route("/api/quick-action", methods=["POST"])
def handle_quick_action():
    data = request.get_json() or {}
    action_type = data.get("action", "")
    mapping = {
        "time":       "what time is it",
        "screenshot": "take a screenshot",
        "weather":    "check current weather",
        "favorites":  "play my favorite songs",
        "mute":       "mute audio",
        "photo":      "take my photo",
        "lock":       "lock my pc",
        "sleep":      "put pc to sleep",
        "empty_bin":  "empty recycle bin",
        "status":     "check system status",
    }
    query = mapping.get(action_type, "what time is it")
    result = process_query(query, is_voice=False)
    return jsonify(result)


@app.route("/api/clear-memory", methods=["POST", "OPTIONS"])
@app.route("/api/history/clear", methods=["POST", "OPTIONS"])
@app.route("/api/memory/clear", methods=["POST", "OPTIONS"])
def clear_memory():
    """Clear conversation history — resets conversations & active state in amigo_memory.json."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    try:
        from ai import clear_conversations_memory
        clear_conversations_memory(clear_profile=False)
    except Exception as e:
        logger.error(f"[Clear Memory] {e}")
        memory = load_memory()
        memory["conversations"] = []
        save_memory(memory)
    broadcaster.broadcast("history_cleared", {})
    return jsonify({"success": True, "message": "Memory cleared"})


@app.route("/api/settings", methods=["GET", "POST", "OPTIONS"])
def handle_settings():
    """Read and persist assistant settings and user preferences to amigo_memory.json."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    memory = load_memory()
    if request.method == "POST":
        data = request.get_json() or {}
        if "user_profile" in data and isinstance(data["user_profile"], dict):
            memory["user_profile"] = data["user_profile"]
        if "theme" in data:
            memory.setdefault("user_profile", {}).setdefault("preferences", {})["theme"] = data["theme"]
        if "model" in data:
            set_active_model(data["model"])
        save_memory(memory)
        broadcaster.broadcast("settings_updated", data)
        return jsonify({"success": True, "message": "Settings saved to Amigo memory"})

    info = get_active_model_info()
    return jsonify({
        "user_profile": memory.get("user_profile", {}),
        "model": info.get("key", "qwen2.5_3b"),
        "model_name": info.get("name", "Qwen 2.5 3B Instruct"),
        "available_models": get_available_models(),
    })


@app.route("/api/user-profile", methods=["GET", "POST", "OPTIONS"])
def handle_user_profile():
    """Read and update user profile directly in amigo_memory.json."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    memory = load_memory()
    if request.method == "POST":
        data = request.get_json() or {}
        user_prof = memory.setdefault("user_profile", {})
        if "name" in data:
            user_prof.setdefault("identity", {})["name"] = data["name"]
        if "role" in data:
            user_prof.setdefault("identity", {})["role"] = data["role"]
        if "preferences" in data:
            user_prof.setdefault("preferences", {}).update(data["preferences"])
        save_memory(memory)
        return jsonify({"success": True, "user_profile": memory.get("user_profile", {})})

    return jsonify(memory.get("user_profile", {}))


@app.route("/api/health", methods=["GET"])
def handle_health():
    """Health check endpoint for UI and system monitors."""
    return jsonify({
        "status": "healthy",
        "engine": "Amigo AI",
        "tts": "Kokoro ONNX",
        "timestamp": time.time(),
    })


@app.route("/api/speak", methods=["POST", "OPTIONS"])
def handle_speak_endpoint():
    """Trigger Kokoro ONNX neural speech on the Amigo backend."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if text:
        speak_ui(text)
    return jsonify({"success": True})


@app.route("/api/shutdown", methods=["POST"])
def shutdown_server():
    """Cleanly shut down the UI server and background voice loop."""
    def kill():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=kill).start()
    return jsonify({"status": "shutting_down"})


@app.route("/api/weather")
def get_weather_endpoint():
    """Live structured weather endpoint for the interactive weather widget."""
    city = request.args.get("city", "").strip()
    data = get_weather_data(city)
    return jsonify(data)


@app.route("/api/media/status")
def get_media_status():
    """Returns the current media state (title, artist, thumbnail, status)."""
    return jsonify(_current_media)


@app.route("/api/media/control", methods=["POST"])
def handle_media_control():
    """Interactive media player control endpoint (play/pause, next, prev, volume, play_query)."""
    global _current_media
    data = request.get_json() or {}
    action = data.get("action", "")

    if action == "play_pause":
        os_automation.play_pause_media()
        new_status = "paused" if _current_media.get("status") == "playing" else "playing"
        _current_media["status"] = new_status
        broadcaster.broadcast("media_update", _current_media)
        return jsonify({"success": True, "status": new_status})
    elif action == "next":
        os_automation.next_track()
        return jsonify({"success": True})
    elif action == "prev":
        os_automation.prev_track()
        return jsonify({"success": True})
    elif action == "volume":
        vol = int(data.get("level", 50))
        _ui_set_volume({"level": vol}, "", "")
        _current_media["volume"] = vol
        broadcaster.broadcast("media_update", _current_media)
        return jsonify({"success": True, "volume": vol})
    elif action == "play_query":
        q = data.get("query", "").strip()
        if q:
            _ui_play_youtube({"query": q}, q, f"Playing {q}")
        return jsonify({"success": True})
    return jsonify({"error": "Unknown action"}), 400


@app.route("/api/models", methods=["GET", "POST"])
def manage_models():
    """Get active/available models (GET) or switch active model (POST)."""
    if request.method == "POST":
        data = request.get_json() or {}
        model_key = data.get("model", "")
        if model_key:
            set_active_model(model_key)
            info = get_active_model_info()
            broadcaster.broadcast("model_change", info)
            return jsonify({"success": True, "active_model": info})
        return jsonify({"error": "No model specified"}), 400

    return jsonify({
        "active": get_active_model_info(),
        "available": get_available_models(),
    })


@app.route("/api/listen", methods=["POST"])
def trigger_listen():
    """Signal the background voice loop to activate for one command cycle."""
    # The background loop is always running; broadcast a state hint to the UI
    broadcaster.broadcast("state_change", {"state": "listening"})
    return jsonify({"status": "listening"})


@app.route("/api/reminders", methods=["GET", "POST", "DELETE"])
def manage_reminders():
    """API endpoint to get active timers/reminders, create a reminder, or cancel all."""
    if request.method == "POST":
        data = request.get_json() or {}
        if "duration" in data:
            res = handle_set_timer(data, data.get("query", ""))
        else:
            res = handle_set_reminder(data, data.get("query", ""))
        return jsonify({"success": True, "message": res})
    elif request.method == "DELETE":
        res = handle_cancel_reminder()
        return jsonify({"success": True, "message": res})
    return jsonify(get_reminders_data())


@app.route("/api/active-state", methods=["GET"])
def get_state_endpoint():
    """Returns the live active working state slots (media, app, search, topic)."""
    return jsonify(get_active_state(clean_expired=True))


@app.route("/api/user-profile", methods=["GET", "POST"])
def manage_user_profile():
    """Get or update structured user profile & preferences."""
    if request.method == "POST":
        data = request.get_json() or {}
        mem = load_memory()
        prof = mem.setdefault("user_profile", {})
        if "identity" in data and isinstance(data["identity"], dict):
            prof.setdefault("identity", {}).update(data["identity"])
        if "preferences" in data and isinstance(data["preferences"], dict):
            prof.setdefault("preferences", {}).update(data["preferences"])
        if "custom_facts" in data and isinstance(data["custom_facts"], list):
            prof["custom_facts"] = data["custom_facts"]
        save_memory(mem)
        return jsonify({"success": True, "profile": prof})
    return jsonify(load_memory().get("user_profile", {}))


# ---------------------------------------------------------------------------
# Server Launch & Graceful Shutdown Binding
# ---------------------------------------------------------------------------
def _cleanup_all():
    """Ensure Amigo background voice engine and all processes exit together."""
    global _running
    _running = False
    logger.info("[ Amigo ] UI Server stopped. Amigo voice assistant stopped.")


atexit.register(_cleanup_all)


def launch_server(port: int = 5000, open_browser: bool = True) -> None:
    def sig_handler(signum, frame):
        print("\n[ AMIGO ] Shutting down UI server and voice assistant...", flush=True)
        _cleanup_all()
        os._exit(0)

    try:
        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)
    except Exception:
        pass

    if open_browser:
        threading.Timer(
            1.5, lambda: webbrowser.open("http://127.0.0.1:" + str(port))
        ).start()
    
    # Initialize Reminders & Timers background scheduler with UI callbacks
    init_reminders(speak_callback=speak_ui, broadcast_callback=broadcaster.broadcast)

    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        _cleanup_all()


if __name__ == "__main__":
    _info = get_active_model_info()
    print("==================================================")
    print("   AMIGO VOICE ASSISTANT - UI DASHBOARD SERVER   ")
    print(f"   [ {_info['name'].upper()} | KOKORO ONNX | AGENTIC ]")
    print("==================================================")
    launch_server(port=5000, open_browser=True)
