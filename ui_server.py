"""
UI Web Dashboard Server & Background Voice Engine for Amigo Assistant.
Clean architectural controller: routes, event streaming, and voice loop.
"""

import sys
import logging

# Configure logging FIRST so messages from all imports are visible
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
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
import uuid

import psutil
from flask import Flask, Response, jsonify, request, send_from_directory
import speech_recognition as sr

from ai import (
    add_to_memory,
    get_active_state,
    load_memory,
    save_memory,
    clear_conversations_memory,
)
from local_llm import (
    get_active_model_info as get_llm_model_info,
    get_agent_action,
    get_clipboard_text,
    set_active_model,
)
from os_automation import (
    play_pause_media,
    next_track,
    prev_track,
    set_volume,
)
from reminder_timer import (
    get_active_data,
    init_reminders,
    handle_set_timer,
    handle_set_reminder,
    handle_cancel_reminder,
)
from tool_registry import (
    UI_TOOL_HANDLERS,
    build_action_cards,
    set_media_update_callback,
    _tool_chat,
)
from tts import (
    speak,
    stop_speaking,
    set_tts_callbacks,
    get_tts_engine_name,
)
from weather import get_weather_data
import rag_engine
from rag_indexer import start_background_indexer

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
    "video_id": "",
    "url": "",
    "status": "idle",
    "volume": 75,
}


def set_assistant_state(state_name: str) -> None:
    global current_state
    current_state = state_name
    broadcaster.broadcast("state_change", {"state": state_name})


# Wire TTS and Tool Registry to broadcaster
set_tts_callbacks(state_cb=set_assistant_state, broadcast_cb=broadcaster.broadcast)


def _on_media_update(media_data: dict):
    global _current_media
    _current_media.update(media_data)
    broadcaster.broadcast("media_update", _current_media)


set_media_update_callback(_on_media_update)


# ---------------------------------------------------------------------------
# Microphone & Voice Recognition Helpers
# ---------------------------------------------------------------------------
_recognizer = sr.Recognizer()
_recognizer.energy_threshold = 400
_recognizer.dynamic_energy_threshold = True
_recognizer.pause_threshold = 0.65
_recognizer.phrase_threshold = 0.2
_mic_calibrated = False


def _calibrate_mic_once():
    """Calibrate ambient noise once at first use."""
    global _mic_calibrated
    if _mic_calibrated:
        return
    try:
        with sr.Microphone() as source:
            logger.info("Calibrating microphone (one-time)...")
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
# Active Model Info Helpers
# ---------------------------------------------------------------------------
def get_active_model_info():
    try:
        info = get_llm_model_info()
        return {
            "key": info.get("key", "qwen-3.5-2b"),
            "name": info.get("name", "Qwen 3.5 2B Instruct"),
            "type": "local_gguf",
            "context_length": 8192,
            "tts_engine": get_tts_engine_name(),
        }
    except Exception:
        return {
            "key": "qwen-3.5-2b",
            "name": "Qwen 3.5 2B Instruct",
            "type": "local_gguf",
            "context_length": 8192,
            "tts_engine": get_tts_engine_name(),
        }


def get_available_models():
    return [
        {"key": "qwen-3.5-2b", "name": "Qwen 3.5 2B Instruct", "status": "active", "type": "local"},
        {"key": "llama-3.2-3b", "name": "Llama 3.2 3B Instruct", "status": "available", "type": "local"},
    ]


# ---------------------------------------------------------------------------
# Core Query Processor (Agentic)
# ---------------------------------------------------------------------------
def _process_query(query: str, is_voice: bool = True, request_id: str | None = None) -> dict:
    """Fully agentic query processor powered by local LLM."""
    set_assistant_state("processing")
    memory = load_memory()
    history = memory.get("conversations", [])
    history = history[-15:]

    clipboard_used = bool(get_clipboard_text())
    llm_started = time.perf_counter()
    actions = get_agent_action(query, conversation_history=history)
    logger.info(
        "[Timing] request_id=%s stage=llm duration_ms=%.1f",
        request_id or "unknown", (time.perf_counter() - llm_started) * 1000,
    )

    combined_spoken = []
    last_tool = "chat"
    last_params = {}
    last_remember = ""
    last_url = None
    response_metadata = {}

    for action in actions:
        tool = action.get("tool", "chat")
        params = action.get("params", {})
        spoken = action.get("speak", "")
        remember = action.get("remember", "")

        if not isinstance(tool, str) or tool not in UI_TOOL_HANDLERS:
            tool = "chat"
        if not isinstance(params, dict):
            params = {}
        last_tool = tool
        last_params = params
        if remember:
            last_remember = remember

        if tool in ("chat", "web_search"):
            spoken = ""

        logger.info(f"[Query Action] tool={tool!r} params={params}")
        broadcaster.broadcast("intent_detected", {"intent": tool.upper(), "params": params})

        handler = UI_TOOL_HANDLERS.get(tool, _tool_chat)
        tool_started = time.perf_counter()
        handler_result = handler(params, query, spoken)
        if isinstance(handler_result, tuple):
            if len(handler_result) == 3:
                res_spoken, res_url, handler_metadata = handler_result
            elif len(handler_result) == 2:
                res_spoken, res_url = handler_result
                handler_metadata = {}
            else:
                res_spoken = handler_result[0] if handler_result else ""
                res_url, handler_metadata = None, {}
        else:
            res_spoken = handler_result or ""
            res_url, handler_metadata = None, {}
        if handler_metadata:
            response_metadata.update(handler_metadata)
        logger.info(
            "[Timing] request_id=%s stage=tool tool=%s duration_ms=%.1f",
            request_id or "unknown", tool, (time.perf_counter() - tool_started) * 1000,
        )

        final_to_speak = res_spoken if res_spoken else spoken
        if final_to_speak:
            speech_to_voice = final_to_speak
            if "matching file" in speech_to_voice and "\n" in speech_to_voice:
                m_count = re.search(r"I found (\d+) matching file", speech_to_voice)
                c_num = m_count.group(1) if m_count else "some"
                speech_to_voice = f"I found {c_num} matching files. Choose one on your screen or say 'Open number 1'."
            speak(speech_to_voice, request_id=request_id)
            combined_spoken.append(speech_to_voice)


        if res_url:
            last_url = res_url

    final_reply = " ".join(combined_spoken).strip() or (f"Completed {last_tool.replace('_', ' ')}." if last_tool != "chat" else "")

    add_to_memory(
        query,
        final_reply or f"Completed {last_tool.replace('_', ' ')}",
        tool=last_tool,
        clipboard_used=clipboard_used,
        remember=last_remember,
    )

    broadcaster.broadcast("chat_message", {
        "sender": "assistant",
        "text": final_reply or f"Completed {last_tool.replace('_', ' ')}",
        "tool": last_tool,
        "url": last_url,
    })

    return {"tool": last_tool, "params": last_params, "response": final_reply, "url": last_url, "metadata": response_metadata}


def process_query(query: str, is_voice: bool = True) -> dict:
    """Safely executes a query with error recovery and request telemetry."""
    request_id = uuid.uuid4().hex[:12]
    request_started = time.perf_counter()
    request_status = "failed"
    logger.info("[Timing] request_id=%s stage=request start", request_id)

    try:
        result = _process_query(query, is_voice=is_voice, request_id=request_id)

        request_status = "completed"
        result.setdefault("metadata", {})["request_id"] = request_id
        result["metadata"]["duration_ms"] = round((time.perf_counter() - request_started) * 1000, 1)
        result["metadata"]["status"] = request_status
        return result
    except Exception as exc:
        logger.exception("[Query Error] %s", exc)
        error_reply = "I couldn't complete that request. Please try again."
        try:
            add_to_memory(query, error_reply, tool="error")
            broadcaster.broadcast("chat_message", {
                "sender": "assistant",
                "text": error_reply,
                "tool": "error",
            })
        except Exception:
            pass
        return {"tool": "error", "params": {}, "response": error_reply, "url": None}
    finally:
        set_assistant_state("idle")


# ---------------------------------------------------------------------------
# Background Voice Loop
# ---------------------------------------------------------------------------
_running = True


def background_voice_loop():
    """Background daemon: wake-word detection + voice command processing."""
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
                speak("Yes, how can I assist you?")
                cmd = take_command_ui()
                if cmd and cmd.lower() != "none" and _running:
                    process_query(cmd, is_voice=True)
        except Exception as e:
            if not _running:
                break
            logger.error(f"[Voice Loop Exception]: {e}")
            time.sleep(1)
    logger.info("[Voice Loop] Background listening engine stopped.")


# ---------------------------------------------------------------------------
# Flask HTTP Routes & API
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
        "model": info.get("name", "Qwen 3.5 2B Instruct"),
        "endpoints": {
            "assistant_process": "/api/assistant/process",
            "action_execute": "/api/action/execute",
            "status": "/api/status",
            "system_stats": "/api/system-stats",
            "history": "/api/history",
        },
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

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    if data.get("isHealthCheck") or data.get("prompt") == "ping_health_check":
        info = get_active_model_info()
        return jsonify({
            "status": "online",
            "message": "Amigo AI Engine online",
            "model": info.get("name", "Qwen 3.5 2B Instruct"),
            "version": "Windows 11 Voice Assistant",
        })

    prompt_value = data.get("prompt", "")
    if not isinstance(prompt_value, str):
        return jsonify({"error": "Prompt must be a string"}), 400
    prompt = prompt_value.strip()
    if not prompt:
        return jsonify({"error": "Empty prompt"}), 400

    broadcaster.broadcast("chat_message", {"sender": "user", "text": prompt})

    result = process_query(prompt, is_voice=False)
    tool = result.get("tool", "chat")
    params = result.get("params", {})
    response_text = result.get("response", "")
    url = result.get("url", "")
    result_metadata = result.get("metadata", {})

    action_cards = build_action_cards(
        tool=tool,
        params=params,
        result_metadata=result_metadata,
        url=url,
        response_text=response_text,
        prompt=prompt,
    )

    has_multiple_cards = bool(action_cards and len(action_cards) > 1 and any(not c.get("selected") for c in action_cards))

    speech_reply = response_text or f"Executing {tool.replace('_', ' ')}"
    if "matching file" in speech_reply and "\n" in speech_reply:
        m_count = re.search(r"I found (\d+) matching file", speech_reply)
        c_num = m_count.group(1) if m_count else "some"
        speech_reply = f"I found {c_num} matching files. Choose one on your screen or say 'Open number 1'."

    formatted_response = {
        "speechReply": speech_reply,
        "displayTitle": prompt,
        "intent": tool,
        "requiresDisambiguation": has_multiple_cards,
        "actionCards": action_cards,
        "contactMatches": [],
        "executionSummary": {
            "status": "completed",
            "headline": "Action Completed",
            "details": response_text or f"Completed {tool.replace('_', ' ')}.",
            "secondaryDetails": url if url else "",
        },
        "metadata": result_metadata,
        "url": url,
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
        try:
            handler_result = handler(payload, data.get("title", ""), "")
            if isinstance(handler_result, tuple):
                spoken = handler_result[0] if len(handler_result) > 0 else ""
                url = handler_result[1] if len(handler_result) > 1 else None
            else:
                spoken = str(handler_result)
                url = None
            return jsonify({"success": True, "message": spoken or "Executed", "url": url})
        except Exception as err:
            logger.exception("[Action Execute Error]: %s", err)
            return jsonify({"success": False, "error": str(err)}), 500
    return jsonify({"success": True, "message": "Action completed"})


@app.route("/api/transcribe", methods=["POST", "OPTIONS"])
def api_transcribe():
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
    """Live system stats for the UI header widget (CPU, RAM, Battery)."""
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
    return jsonify(rag_engine.load_memory())


@app.route("/api/query", methods=["POST"])
def handle_query():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    user_query = str(data.get("query", "")).strip()
    if not user_query:
        return jsonify({"error": "Empty query"}), 400
    broadcaster.broadcast("chat_message", {"sender": "user", "text": user_query})
    result = process_query(user_query, is_voice=False)
    return jsonify(result)


@app.route("/api/quick-action", methods=["POST"])
def handle_quick_action():
    data = request.get_json() or {}
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
    query = mapping.get(data.get("action", ""), "what time is it")
    result = process_query(query, is_voice=False)
    return jsonify(result)


@app.route("/api/clear-memory", methods=["POST", "OPTIONS"])
@app.route("/api/history/clear", methods=["POST", "OPTIONS"])
@app.route("/api/memory/clear", methods=["POST", "OPTIONS"])
def clear_memory():
    """Clear conversation history & RAG vector memory — resets conversations & active state."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    try:
        data = request.get_json(silent=True) or {}
        clear_profile = bool(data.get("clear_profile", False))
        clear_facts = bool(data.get("clear_facts", False))

        clear_conversations_memory(clear_profile=clear_profile)
        if clear_facts and not clear_profile:
            rag_engine.clear_user_facts()

        logger.info("[Clear Memory] Cleared RAG conversation memory and active state successfully.")
    except Exception as e:
        logger.error(f"[Clear Memory] Error clearing conversations: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    broadcaster.broadcast("history_cleared", {})
    return jsonify({"success": True, "message": "Memory cleared"})


@app.route("/api/settings", methods=["GET", "POST", "OPTIONS"])
def handle_settings():
    """Read and persist assistant settings and user preferences."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    memory = load_memory()
    if request.method == "POST":
        data = request.get_json() or {}
        ui_settings = memory.setdefault("ui_settings", {})
        for k in (
            "isDark", "soundEnabled", "colorTheme", "visualizerMode",
            "textAnimationStyle", "pluginMode", "greetingText",
            "autoCycleGreetings", "autoCycleInterval", "backendConfig",
            "theme", "autoSpeech", "model"
        ):
            if k in data:
                ui_settings[k] = data[k]
        if "user_profile" in data and isinstance(data["user_profile"], dict):
            memory["user_profile"] = data["user_profile"]
        if "theme" in data:
            memory.setdefault("user_profile", {}).setdefault("preferences", {})["theme"] = data["theme"]
        if "model" in data:
            set_active_model(data["model"])
        save_memory(memory)
        broadcaster.broadcast("settings_updated", data)
        return jsonify({"success": True, "message": "Settings saved to Amigo memory", "ui_settings": ui_settings})

    info = get_active_model_info()
    return jsonify({
        "ui_settings": memory.get("ui_settings", {}),
        "user_profile": memory.get("user_profile", {}),
        "model": info.get("key", "qwen-3.5-2b"),
        "model_name": info.get("name", "Qwen 3.5 2B Instruct"),
        "available_models": get_available_models(),
    })



@app.route("/api/user-profile", methods=["GET", "POST", "OPTIONS"])
def handle_user_profile():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    memory = load_memory()
    if request.method == "POST":
        data = request.get_json() or {}
        user_prof = memory.setdefault("user_profile", {})
        if "identity" in data and isinstance(data["identity"], dict):
            user_prof.setdefault("identity", {}).update(data["identity"])
        if "custom_facts" in data and isinstance(data["custom_facts"], list):
            user_prof["custom_facts"] = data["custom_facts"]
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
    return jsonify({
        "status": "healthy",
        "engine": "Amigo AI",
        "tts": get_tts_engine_name(),
        "timestamp": time.time(),
    })


@app.route("/api/speak", methods=["POST", "OPTIONS"])
def handle_speak_endpoint():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if text:
        speak(text)
    return jsonify({"success": True})


@app.route("/api/shutdown", methods=["POST"])
def shutdown_server():
    def kill():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=kill).start()
    return jsonify({"status": "shutting_down"})


@app.route("/api/weather")
def get_weather_endpoint():
    city = request.args.get("city", "").strip()
    data = get_weather_data(city)
    return jsonify(data)


@app.route("/api/media/status")
def get_media_status():
    return jsonify(_current_media)


@app.route("/api/media/control", methods=["POST"])
def handle_media_control():
    global _current_media
    data = request.get_json() or {}
    action = data.get("action", "")

    if action == "play_pause":
        play_pause_media()
        new_status = "paused" if _current_media.get("status") == "playing" else "playing"
        _current_media["status"] = new_status
        broadcaster.broadcast("media_update", _current_media)
        return jsonify({"success": True, "status": new_status})
    elif action == "next":
        next_track()
        return jsonify({"success": True})
    elif action == "prev":
        prev_track()
        return jsonify({"success": True})
    elif action == "volume":
        vol = int(data.get("level", 50))
        set_volume(vol)
        _current_media["volume"] = vol
        broadcaster.broadcast("media_update", _current_media)
        return jsonify({"success": True, "volume": vol})
    elif action == "play_query":
        q = data.get("query", "").strip()
        if q:
            handler = UI_TOOL_HANDLERS.get("play_youtube")
            if handler:
                handler({"query": q}, q, f"Playing {q}")
        return jsonify({"success": True})
    return jsonify({"error": "Unknown action"}), 400


@app.route("/api/models", methods=["GET", "POST"])
def manage_models():
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
    broadcaster.broadcast("state_change", {"state": "listening"})
    return jsonify({"status": "listening"})


@app.route("/api/reminders", methods=["GET", "POST", "DELETE"])
def manage_reminders():
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
    return jsonify(get_active_data())


@app.route("/api/active-state", methods=["GET"])
def get_state_endpoint():
    return jsonify(get_active_state(clean_expired=True))


# ---------------------------------------------------------------------------
# Server Launch & Graceful Shutdown Binding
# ---------------------------------------------------------------------------
def _cleanup_all():
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
        import webbrowser
        threading.Timer(
            1.5, lambda: webbrowser.open("http://127.0.0.1:" + str(port))
        ).start()

    init_reminders(speak_callback=speak, broadcast_callback=broadcaster.broadcast)

    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        _cleanup_all()


# ---------------------------------------------------------------------------
# RAG, Email & Calendar API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/rag/status")
def rag_status_endpoint():
    """RAG index statistics."""
    stats = rag_engine.get_index_stats()
    try:
        from rag_indexer import get_indexer
        indexer = get_indexer(rag_engine)
        idx_status = indexer.get_status()
        stats["indexer"] = idx_status
        stats["is_indexing"] = idx_status.get("is_indexing", False)
    except Exception:
        pass
    return jsonify(stats)


@app.route("/api/rag/search", methods=["POST"])
def rag_search_endpoint():
    """Semantic search across all RAG collections."""
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    collections = data.get("collections")  # optional filter
    top_k = int(data.get("top_k", 5))
    if not query:
        return jsonify({"error": "Empty query"}), 400
    results = rag_engine.search(query, target_collections=collections, top_k=top_k)
    return jsonify({"query": query, "results": results})


@app.route("/api/rag/reindex", methods=["POST"])
def rag_reindex_endpoint():
    """Trigger manual re-indexing."""
    try:
        from rag_indexer import get_indexer
        indexer = get_indexer(rag_engine)
        import threading as _th
        _th.Thread(target=indexer.full_index, daemon=True).start()
        return jsonify({"success": True, "message": "Re-indexing started in background."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/emails")
def emails_endpoint():
    """Fetch recent emails."""
    try:
        from mail_integration import get_recent_emails, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook not available"}), 503
        count = int(request.args.get("count", 5))
        return jsonify({"emails": get_recent_emails(count)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/emails/unread")
def emails_unread_endpoint():
    """Unread email count + previews."""
    try:
        from mail_integration import get_unread_count, get_unread_emails, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook not available"}), 503
        count = get_unread_count()
        emails = get_unread_emails(min(count, 10)) if count > 0 else []
        return jsonify({"unread_count": count, "emails": emails})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/emails/search", methods=["POST"])
def emails_search_endpoint():
    """Search emails by keyword."""
    try:
        from mail_integration import search_emails, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook not available"}), 503
        data = request.get_json() or {}
        q = data.get("query", "").strip()
        if not q:
            return jsonify({"error": "Empty query"}), 400
        return jsonify({"query": q, "results": search_emails(q)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar")
def calendar_endpoint():
    """Today's calendar events."""
    try:
        from calendar_integration import get_todays_events, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook Calendar not available"}), 503
        return jsonify({"events": get_todays_events()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar/upcoming")
def calendar_upcoming_endpoint():
    """Upcoming calendar events."""
    try:
        from calendar_integration import get_upcoming_events, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook Calendar not available"}), 503
        days = int(request.args.get("days", 7))
        return jsonify({"events": get_upcoming_events(days=days)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    _info = get_active_model_info()
    print("==================================================")
    print("   AMIGO VOICE ASSISTANT - UI DASHBOARD SERVER   ")
    print(f"   [ {_info['name'].upper()} | {_info['tts_engine'].upper()} | RAG + AGENTIC ]")
    print("==================================================")

    # Initialize RAG engine and start background indexer
    print("[ AMIGO ] Initializing RAG memory engine...", flush=True)
    rag_engine.init_rag()
    start_background_indexer(rag_engine, interval_minutes=30)
    print("[ AMIGO ] RAG engine ready. Background indexer started.", flush=True)

    launch_server(port=5000, open_browser=True)
