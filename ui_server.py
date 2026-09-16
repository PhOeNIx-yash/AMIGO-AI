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
# Silence noisy third-party dependency spam in terminal
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("primp").setLevel(logging.WARNING)
logging.getLogger("phonemizer").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)

print("[ AMIGO UI SERVER ] Starting up...", flush=True)

import atexit
import base64
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

from ai import (
    add_to_memory,
    get_active_state,
    load_memory,
    save_memory,
    clear_conversations_memory,
    set_thinking_enabled,
    is_thinking_enabled,
    get_last_thought,
)
from local_llm import (
    get_active_model_info as get_llm_model_info,
    get_available_models as get_llm_available_models,
    get_agent_action,
    get_clipboard_text,
    init_local_llm,
    is_vision_ready,
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
    transcribe_b64,
)
from weather import get_weather_data
import rag_engine
from rag_indexer import start_background_indexer
from network_utils import is_internet_connected

print("[ AMIGO UI SERVER ] All imports loaded.", flush=True)

try:
    _init_settings = rag_engine.load_profile().get("ui_settings", {})
    set_thinking_enabled(_init_settings.get("thinkingEnabled", False))
except Exception:
    pass

logger = logging.getLogger("amigo.ui_server")

_RE_FILE_MATCH_COUNT = re.compile(r"I found (\d+) matching file")

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


# Wire TTS, Tool Registry, and Hotkey Service to broadcaster
set_tts_callbacks(state_cb=set_assistant_state, broadcast_cb=broadcaster.broadcast)
try:
    import hotkey_service
    hotkey_service.set_broadcast_callback(broadcaster.broadcast)
except Exception:
    pass


def _on_media_update(media_data: dict):
    global _current_media
    _current_media.update(media_data)
    broadcaster.broadcast("media_update", _current_media)


set_media_update_callback(_on_media_update)


# ---------------------------------------------------------------------------
# Active Model Info Helpers
# ---------------------------------------------------------------------------
def get_active_model_info():
    try:
        info = get_llm_model_info()
        vision_ok = is_vision_ready()
        name = info.get("name", "MiniCPM 5 2B")
        return {
            "key": info.get("key", "minicpm5-2b"),
            "name": name,
            "type": "local_gguf",
            "context_length": 8192,
            "tts_engine": get_tts_engine_name(),
            "vision_ready": vision_ok,
            "vision_mode": f"Native Multimodal ({name})" if vision_ok else "OCR Fallback (Windows Media OCR)",
            "hotkey": "Alt+V",
        }
    except Exception:
        vision_ok = is_vision_ready()
        return {
            "key": "minicpm5-2b",
            "name": "MiniCPM 5 2B",
            "type": "local_gguf",
            "context_length": 8192,
            "tts_engine": get_tts_engine_name(),
            "vision_ready": vision_ok,
            "vision_mode": "OCR Fallback (Windows Media OCR)",
            "hotkey": "Alt+V",
        }


def get_available_models():
    try:
        active_key = get_active_model_info().get("key", "minicpm5-2b")
        models_dict = get_llm_available_models()
        return [
            {
                "key": k,
                "name": v["name"],
                "status": "active" if k == active_key else ("downloaded" if v.get("downloaded") else "available"),
                "type": "local",
            }
            for k, v in models_dict.items()
        ]
    except Exception:
        return [
            {"key": "minicpm5-2b", "name": "MiniCPM 5 2B", "status": "active", "type": "local"},
        ]


# ---------------------------------------------------------------------------
# Core Query Processor (Agentic)
# ---------------------------------------------------------------------------
def _process_query(query: str, is_voice: bool = True, request_id: str | None = None, display_prompt: str | None = None) -> dict:
    """Fully agentic query processor powered by local LLM."""
    set_assistant_state("processing")
    history = rag_engine.get_recent_conversations(15)

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
        spoken = action.get("speak", "") or (params.get("speak", "") if isinstance(params, dict) else "")
        remember = action.get("remember", "")

        if not isinstance(tool, str) or tool not in UI_TOOL_HANDLERS:
            tool = "chat"
        if not isinstance(params, dict):
            params = {}
        last_tool = tool
        last_params = params
        if remember:
            last_remember = remember

        if tool == "web_search":
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
                m_count = _RE_FILE_MATCH_COUNT.search(speech_to_voice)
                c_num = m_count.group(1) if m_count else "some"
                speech_to_voice = f"I found {c_num} matching files. Choose one on your screen or say 'Open number 1'."
            speak(speech_to_voice, request_id=request_id)
            combined_spoken.append(speech_to_voice)


        if res_url:
            last_url = res_url

    final_reply = " ".join(combined_spoken).strip() or (f"Completed {last_tool.replace('_', ' ')}." if last_tool != "chat" else "")

    add_to_memory(
        display_prompt or query,
        final_reply or f"Completed {last_tool.replace('_', ' ')}",
        tool=last_tool,
        clipboard_used=clipboard_used,
        remember=last_remember,
    )

    last_thought = get_last_thought()
    if last_thought:
        response_metadata["thought"] = last_thought

    broadcaster.broadcast("chat_message", {
        "sender": "assistant",
        "text": final_reply or f"Completed {last_tool.replace('_', ' ')}",
        "tool": last_tool,
        "url": last_url,
        "thought": last_thought,
    })

    return {"tool": last_tool, "params": last_params, "response": final_reply, "url": last_url, "metadata": response_metadata, "thought": last_thought}


def process_query(query: str, is_voice: bool = True, display_prompt: str | None = None) -> dict:
    """Safely executes a query with error recovery and request telemetry."""
    request_id = uuid.uuid4().hex[:12]
    request_started = time.perf_counter()
    request_status = "failed"
    logger.info("[Timing] request_id=%s stage=request start", request_id)

    try:
        result = _process_query(query, is_voice=is_voice, request_id=request_id, display_prompt=display_prompt)

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


_running = True


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
        "model": info.get("name", "MiniCPM 5 2B"),
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


UPLOAD_DIR = os.environ.get(
    "AMIGO_UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag_data", "uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".svg", ".ico"}


@app.route("/api/upload", methods=["POST", "OPTIONS"])
def api_upload():
    """Upload and index documents, PDFs, or images for assistant analysis."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = None
    saved_path = None
    file_bytes = None

    # 1. Multipart/form-data
    if "file" in request.files:
        f = request.files["file"]
        if f.filename:
            raw_filename = os.path.basename(f.filename)
            safe_name = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)}"
            saved_path = os.path.join(UPLOAD_DIR, safe_name)
            f.save(saved_path)
            filename = raw_filename
            try:
                with open(saved_path, "rb") as rf:
                    file_bytes = rf.read()
            except Exception:
                pass

    # 2. JSON Base64 payload
    if not saved_path and request.is_json:
        data = request.get_json(silent=True) or {}
        raw_filename = os.path.basename(data.get("filename", "upload.bin"))
        b64_data = data.get("fileData", "")
        if b64_data:
            if "," in b64_data:
                b64_data = b64_data.split(",", 1)[1]
            try:
                file_bytes = base64.b64decode(b64_data)
                safe_name = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)}"
                saved_path = os.path.join(UPLOAD_DIR, safe_name)
                with open(saved_path, "wb") as wf:
                    wf.write(file_bytes)
                filename = raw_filename
            except Exception as be:
                logger.error("[Upload] Base64 decode error: %s", be)

    if not saved_path or not os.path.exists(saved_path):
        return jsonify({"success": False, "error": "No file uploaded or invalid file data"}), 400

    ext = os.path.splitext(filename or saved_path)[1].lower()
    file_size = os.path.getsize(saved_path)

    # Classify file type dynamically
    if ext == ".pdf":
        file_type = "pdf"
    elif ext in IMAGE_EXTENSIONS:
        file_type = "image"
    elif ext in rag_engine.SUPPORTED_EXTENSIONS:
        file_type = "document"
    else:
        test_txt = rag_engine.extract_text(saved_path)
        file_type = "document" if (test_txt and len(test_txt.strip()) > 0) else "generic"

    extracted_preview = ""
    ocr_text = ""

    # Process and Index according to type
    if file_type in ("pdf", "document"):
        try:
            extracted_text = rag_engine.extract_text(saved_path)
            extracted_preview = (extracted_text or "").strip()[:800]
            # Index document into ChromaDB vector store
            indexed = rag_engine.index_document(saved_path)
            # Update active_file state
            rag_engine.update_active_state("active_file", {
                "path": saved_path,
                "name": filename,
                "type": file_type,
            })
            logger.info("[Upload] Document '%s' indexed successfully (size: %d, indexed: %s)", filename, file_size, indexed)
        except Exception as e:
            logger.error("[Upload] Error indexing document %s: %s", saved_path, e)

    elif file_type == "image":
        try:
            from PIL import Image
            from screen_vision import read_text_from_image, analyze_image
            ocr_text = ""
            with Image.open(saved_path) as img:
                ocr_text = read_text_from_image(img) or ""
            # Visual comprehension with image description and OCR
            visual_desc = analyze_image(saved_path, question="Summarize and describe what is visible in this image.")
            extracted_preview = visual_desc or ocr_text.strip()[:800]
            rag_engine.update_active_state("active_file", {
                "path": saved_path,
                "name": filename,
                "type": "image",
                "description": visual_desc,
                "ocr_text": ocr_text.strip()[:1000],
            })
            logger.info("[Upload] Image '%s' processed (OCR chars: %d, vision: %s)", filename, len(ocr_text), bool(visual_desc))
        except Exception as e:
            logger.debug("[Upload] Image analysis note for %s: %s", saved_path, e)

    return jsonify({
        "success": True,
        "filename": filename,
        "filepath": saved_path,
        "fileType": file_type,
        "size": file_size,
        "extractedPreview": extracted_preview or ocr_text,
        "message": f"Successfully uploaded and indexed {filename}",
    })


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
            "model": info.get("name", "MiniCPM 5 2B"),
            "version": "Windows 11 Voice Assistant",
        })

    prompt_value = data.get("prompt", "")
    prompt = str(prompt_value).strip() if prompt_value is not None else ""
    attachment = data.get("attachment")

    # If prompt is empty but attachment is supplied, default to summarizing the attachment
    if not prompt:
        if isinstance(attachment, dict) and attachment.get("filename"):
            prompt = f"Analyze {attachment.get('filename')}"
        else:
            return jsonify({"error": "Empty prompt"}), 400

    # Enrich query with attached file / image context
    attached_file_context = ""
    if isinstance(attachment, dict):
        att_filename = attachment.get("filename", "")
        att_path = attachment.get("filepath", "")
        att_type = attachment.get("fileType", "")
        att_preview = attachment.get("extractedPreview", "")
        if att_path and os.path.exists(att_path):
            if att_type in ("pdf", "document"):
                doc_ctx = rag_engine.build_file_context(att_path, prompt)
                if not doc_ctx:
                    doc_ctx = rag_engine.extract_text(att_path)[:3000]
                attached_file_context = f"[Attached Document: {att_filename}]\n{doc_ctx}"
            elif att_type == "image":
                attached_file_context = f"[Attached Image: {att_filename}]\nExtracted Visual OCR Text:\n{att_preview}"
        elif att_preview:
            attached_file_context = f"[Attached File: {att_filename}]\nContent:\n{att_preview}"

    effective_query = prompt
    if attached_file_context:
        effective_query = f"{attached_file_context}\n\nUser Question / Task: {prompt}"

    broadcaster.broadcast("chat_message", {"sender": "user", "text": prompt})

    result = process_query(effective_query, is_voice=False, display_prompt=prompt)
    tool = result.get("tool", "chat")
    params = result.get("params", {})
    response_text = result.get("response", "")
    url = result.get("url", "")
    result_metadata = result.get("metadata", {})

    # Action cards are only for interactive tools (timer, weather, file picker), not for document Q&A/analysis
    action_cards = []
    if not attachment:
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
        m_count = _RE_FILE_MATCH_COUNT.search(speech_reply)
        c_num = m_count.group(1) if m_count else "some"
        speech_reply = f"I found {c_num} matching files. Choose one on your screen or say 'Open number 1'."

    status = result_metadata.get("status")
    if not status:
        if not is_internet_connected() and ("not connected to the internet" in speech_reply.lower() or "offline" in speech_reply.lower()):
            status = "offline"
        elif result_metadata.get("error") or "error" in result_metadata:
            status = "failed"
        else:
            status = "completed"

    if status == "offline":
        headline = "Offline"
    elif status == "failed":
        headline = "Action Failed"
    else:
        headline = "Action Completed"

    formatted_response = {
        "speechReply": speech_reply,
        "displayTitle": prompt,
        "intent": tool,
        "requiresDisambiguation": has_multiple_cards,
        "actionCards": action_cards,
        "contactMatches": [],
        "executionSummary": {
            "status": status,
            "headline": headline,
            "details": response_text or f"{headline}: {tool.replace('_', ' ')}.",
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
    audio_b64 = data.get("audioData") or data.get("audio") or ""
    if audio_b64:
        try:
            text = transcribe_b64(audio_b64)
            return jsonify({"transcription": text})
        except Exception as e:
            logger.error("[STT API] Transcription error: %s", e)
            return jsonify({"transcription": "", "error": str(e)}), 500
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
            "online":  is_internet_connected(),
        })
    except Exception as e:
        logger.error(f"[System Stats] {e}")
        return jsonify({"cpu": 0, "ram": 0, "battery": None, "plugged": None, "online": False})


@app.route("/api/internal/broadcast", methods=["POST", "OPTIONS"])
def api_internal_broadcast():
    """Cross-process SSE broadcast bridge for hotkey and external assistant triggers."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "chat_message")
    data = payload.get("data", {})
    broadcaster.broadcast(event_type, data)
    return jsonify({"status": "ok", "broadcasted": event_type})


@app.route("/api/history")
def get_history():
    """Fetch all conversation history from RAG engine with fresh cache check."""
    rag_engine.invalidate_conversations_cache()
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
            "theme", "autoSpeech", "model", "thinkingEnabled"
        ):
            if k in data:
                ui_settings[k] = data[k]
        if "thinkingEnabled" in data:
            set_thinking_enabled(bool(data["thinkingEnabled"]))
            ui_settings["thinkingEnabled"] = bool(data["thinkingEnabled"])
        elif "backendConfig" in data and isinstance(data["backendConfig"], dict) and "thinkingEnabled" in data["backendConfig"]:
            set_thinking_enabled(bool(data["backendConfig"]["thinkingEnabled"]))
            ui_settings["thinkingEnabled"] = bool(data["backendConfig"]["thinkingEnabled"])

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
        "model": info.get("key", "minicpm5-2b"),
        "model_name": info.get("name", "MiniCPM 5 2B"),
        "thinking_enabled": is_thinking_enabled(),
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
        import flask.cli
        flask.cli.show_server_banner = lambda *args, **kwargs: None
    except Exception:
        pass

    try:
        from werkzeug.serving import run_simple
        run_simple("0.0.0.0", port, app, use_reloader=False, threaded=True)
    except Exception as e:
        logger.warning(f"[Server] run_simple fallback: {e}")
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
    """Trigger manual re-indexing with live SSE progress broadcasting."""
    try:
        data = request.get_json(silent=True) or {}
        force = data.get("force", True)
        from rag_indexer import get_indexer
        indexer = get_indexer(rag_engine)

        def _progress_cb(processed, total, indexed, skipped, fname):
            pct = round((processed / max(total, 1)) * 100, 1)
            broadcaster.broadcast("rag_indexing_progress", {
                "is_indexing": True,
                "files_total": total,
                "files_processed": processed,
                "files_indexed": indexed,
                "files_skipped": skipped,
                "progress_percent": pct,
                "current_file": fname,
                "status_message": f"Processing ({processed}/{total}): {fname}",
            })

        def _run():
            # Initial announcement
            broadcaster.broadcast("rag_indexing_progress", {
                "is_indexing": True,
                "files_total": 0,
                "files_processed": 0,
                "files_indexed": 0,
                "files_skipped": 0,
                "progress_percent": 0.0,
                "current_file": "Scanning directories...",
                "status_message": "Scanning directories...",
            })
            indexer.full_index(force=force, progress_cb=_progress_cb)
            st = indexer.get_status()
            broadcaster.broadcast("rag_indexing_progress", {
                "is_indexing": False,
                "files_total": st.get("files_total", 0),
                "files_processed": st.get("files_processed", 0),
                "files_indexed": st.get("files_indexed", 0),
                "files_skipped": st.get("files_skipped", 0),
                "progress_percent": 100.0,
                "current_file": "",
                "status_message": st.get("status_message", "Complete"),
            })

        import threading as _th
        _th.Thread(target=_run, daemon=True, name="rag-manual-reindex").start()
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


@app.route("/api/hotkey/status")
def api_hotkey_status():
    """Status of global Alt+V vision hotkey service."""
    try:
        from hotkey_service import is_hotkey_service_running
        return jsonify({
            "hotkey": "Alt+V",
            "running": is_hotkey_service_running(),
            "description": "Press Alt+V anywhere across Windows to wake Amigo without opening browser",
        })
    except Exception as e:
        return jsonify({"running": False, "error": str(e)})


if __name__ == "__main__":
    _info = get_active_model_info()
    print("==================================================")
    print("   AMIGO VOICE ASSISTANT - UI DASHBOARD SERVER   ")
    print(f"   [ {_info['name'].upper()} | {_info['tts_engine'].upper()} | RAG + AGENTIC ]")
    print("==================================================")
    
    # Start Alt+V global wake hotkey service
    try:
        import hotkey_service
        hotkey_service.set_broadcast_callback(broadcaster.broadcast)
        hotkey_service.start_hotkey_service()
    except Exception as e:
        logger.warning(f"[Hotkey Service] Could not start: {e}")

    # Initialize RAG engine and start background indexer
    print("[ AMIGO ] Initializing RAG memory engine...", flush=True)
    rag_engine.init_rag()
    start_background_indexer(rag_engine, interval_minutes=30)
    print("[ AMIGO ] RAG engine ready. Background indexer started.", flush=True)

    # Pre-load local AI model so the very first command has zero cold-start delay
    print("[ AMIGO ] Loading local AI model into memory...", flush=True)
    init_local_llm()
    print("[ AMIGO ] Local AI model ready.", flush=True)

    launch_server(port=5000, open_browser=True)
