"""
UI Web Dashboard Server & Background Voice Engine for Amigo Assistant.
Fixes:
- Separates attachment document context from router query so 3B router & vector search stay fast and accurate.
- Records `/api/action/execute` turns into conversation memory so follow-up questions work after clicking UI cards.
- Eliminates the duplicate 65-line action-chaining loop by delegating to `tool_registry.execute_actions`.
- Non-blocking system stats & clipboard checks for instant API response times.
"""

import atexit
import base64
import json
import logging
import os
import queue
import re
import signal
import sys
import threading
import time
import uuid
import webbrowser
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)

for _noisy_logger in (
    "werkzeug", "primp", "phonemizer", "urllib3", "chromadb",
    "pdfminer", "pdfminer.pdffont", "pdfminer.pdfinterp", "pdfminer.pdfpage",
    "pdfminer.pdfdocument", "pypdf", "pdfplumber",
    "httpx", "httpcore", "sentence_transformers", "transformers", "huggingface_hub",
    "pocket_tts", "pocket_tts.models.tts_model", "pocket_tts.utils.utils", "pocket_tts.models", "pocket_tts.utils",
):
    _l = logging.getLogger(_noisy_logger)
    _l.setLevel(logging.ERROR)
    _l.propagate = False

print("[ AMIGO UI SERVER ] Starting up...", flush=True)

import psutil
from flask import Flask, Response, jsonify, request, send_from_directory

import amigo.core.rag_engine as rag_engine
from amigo.core.llm_agent import (
    _CLIPBOARD_KEYWORDS,
    _get_internet_status,
    add_to_memory,
    clear_conversations_memory,
    get_active_model_info as get_llm_model_info,
    get_active_state,
    get_available_models as get_llm_available_models,
    get_clipboard_text,
    get_last_thought,
    get_quick_feedback,
    init_local_llm,
    is_thinking_enabled,
    is_vision_ready,
    load_memory,
    query_local_llm,
    save_memory,
    set_active_model,
    set_thinking_enabled,
)
from amigo.core.proactive_intelligence import (
    get_proactive_intelligence,
    init_proactive_intelligence,
    shutdown_proactive_intelligence,
)
from amigo.core.rag_indexer import get_indexer, start_background_indexer
from amigo.core.reminder_timer import (
    get_active_data,
    handle_cancel_reminder,
    handle_set_reminder,
    handle_set_timer,
    init_reminders,
)
from amigo.services.os_automation import next_track, play_pause_media, prev_track, set_volume
from amigo.services.weather import get_weather_data
from amigo.utils.tool_registry import (
    _ROUTER_STOP,
    UI_TOOL_HANDLERS,
    build_action_cards,
    execute_actions,
    execute_tool,
    get_agent_action,
    get_static_router_prefix,
    set_media_update_callback,
    set_ui_broadcast_callback,
)
from amigo.utils.tts import (
    get_active_voice,
    get_tts_engine_name,
    get_voice_catalog,
    set_tts_callbacks,
    set_voice,
    speak,
    stop_speaking,
    transcribe_b64,
)

print("[ AMIGO UI SERVER ] All imports loaded.", flush=True)

try:
    _init_settings = rag_engine.load_profile().get("ui_settings", {})
    set_thinking_enabled(_init_settings.get("thinkingEnabled", False))
except Exception:
    pass

# Prime psutil CPU counter so subsequent calls with interval=None are 0ms non-blocking
try:
    psutil.cpu_percent(interval=None)
except Exception:
    pass

logger = logging.getLogger("amigo.ui_server")
_RE_FILE_MATCH_COUNT = re.compile(r"I found (\d+) matching file")
_SILENT_TOOLS = {"chat", "stop", "stop_speaking", "pause_media", "resume_media", "next_track", "prev_track", "blocked"}

app = Flask(__name__, template_folder="templates", static_folder="static")


# ═══════════════════════════════════════════════════════════════
#  SSE Event Broadcaster & State Management
# ═══════════════════════════════════════════════════════════════

class EventBroadcaster:
    def __init__(self):
        self.listeners: list[queue.Queue] = []
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=50)
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def broadcast(self, event_type: str, data: dict | None = None) -> None:
        payload = {"type": event_type, **(data or {})}
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


set_tts_callbacks(state_cb=set_assistant_state, broadcast_cb=broadcaster.broadcast)
try:
    from amigo.ui import hotkey_service
    hotkey_service.set_broadcast_callback(broadcaster.broadcast)
except Exception:
    pass


def _on_media_update(media_data: dict) -> None:
    _current_media.update(media_data)
    broadcaster.broadcast("media_update", _current_media)


set_media_update_callback(_on_media_update)
set_ui_broadcast_callback(broadcaster.broadcast)


# ═══════════════════════════════════════════════════════════════
#  Model, Voice & Speech Formatting Helpers
# ═══════════════════════════════════════════════════════════════

def get_active_model_info() -> dict:
    try:
        info = get_llm_model_info() or {}
    except Exception:
        models = get_llm_available_models() or {}
        info = next(iter(models.values()), {})

    name = info.get("name", "Spark-X2.5 4B")
    key = info.get("key", "spark-x2.5-4b")
    vision_ok = is_vision_ready()
    ctx_len = int(os.getenv("AMIGO_LLM_N_CTX", "8192"))
    return {
        "key": key,
        "name": name,
        "type": "local_gguf",
        "context_length": ctx_len,
        "tts_engine": get_tts_engine_name(),
        "system1_router": f"LLM Agent ({name})",
        "vision_ready": vision_ok,
        "vision_mode": f"Native Multimodal ({name})" if vision_ok else "OCR Fallback (Windows Media OCR)",
        "hotkey": "Alt+V",
        "accelerator": info.get("accelerator", "NVIDIA GeForce RTX 3050 (CUDA)"),
        "gpu_offload": info.get("gpu_offload", True),
        "flash_attn": info.get("flash_attn", True),
    }


def get_available_models() -> list[dict]:
    try:
        active_key = get_active_model_info().get("key", "spark-x2.5-4b")
        return [
            {
                "key": k,
                "name": v.get("name", k),
                "status": "active" if k == active_key else ("downloaded" if v.get("downloaded") else "available"),
                "type": "local",
            }
            for k, v in (get_llm_available_models() or {}).items()
        ]
    except Exception:
        return [
            {"key": "spark-x2.5-4b", "name": "Spark-X2.5 4B", "status": "active", "type": "local"},
        ]


def _apply_voice_preference(memory: dict, target_voice: str) -> str:
    """Single helper to update voice across TTS engine and memory dicts without repetition."""
    v_clean = target_voice.strip().lower()
    v_name = set_voice(v_clean)
    memory.setdefault("ui_settings", {})["voice"] = v_clean
    memory.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = v_clean
    memory.setdefault("preferences", {})["voice"] = v_clean
    return v_name


_CONTRACTIONS = [
    (re.compile(r"\bI have\b", re.I), "I've"),
    (re.compile(r"\bIt is\b", re.I), "It's"),
    (re.compile(r"\bThat is\b", re.I), "That's"),
    (re.compile(r"\bHere is\b", re.I), "Here's"),
    (re.compile(r"\bThere is\b", re.I), "There's"),
    (re.compile(r"\bDo not\b", re.I), "Don't"),
    (re.compile(r"\bCannot\b", re.I), "Can't"),
    (re.compile(r"\bWill not\b", re.I), "Won't"),
    (re.compile(r"\bYou will\b", re.I), "You'll"),
    (re.compile(r"\bWe have\b", re.I), "We've"),
]


def format_conversational_speech(text: str) -> str:
    """Transforms structured/table/list output into natural spoken speech."""
    if not text or len(text.strip()) < 35:
        return text

    spoken = text
    for pat, rep in _CONTRACTIONS:
        spoken = pat.sub(rep, spoken)

    if "|" in spoken and ("|---" in spoken or spoken.count("|") >= 4):
        table_raw = re.sub(r"\|\s*\|+", "|\n|", spoken)
        lines = [ln.strip() for ln in table_raw.split("\n") if ln.strip()]
        title, entities, found_sep = "", [], False
        for line in lines:
            if "|" not in line:
                clean_l = re.sub(r"#{1,6}\s*", "", line).strip()
                if clean_l and not title:
                    title = clean_l
            elif re.search(r"[\-:]\s*\|", line):
                found_sep = True
            elif found_sep:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if cells:
                    entities.append(cells[0])

        clean_title = re.sub(r"(\(.*?\)|##.*|:$)", "", title).replace("->", "to").strip() or "details"
        if entities:
            return f"Here are the {clean_title.lower()} for {', '.join(entities[:4])}. I've displayed the full comparison on your screen."
        return f"Here are the {clean_title.lower()}. I've displayed the full breakdown on your screen."

    raw_items = re.findall(r"(?:^|\n|\s)\d+\.\s+[\"'*]*([^\"'\n\–\-:]{3,40})[\"'*]*\s*[\–\-:]", spoken)
    clean_items = [it.strip().strip("\"'") for it in raw_items if it.strip()]
    if len(clean_items) >= 3:
        intro_m = re.match(r"^(.*?)(?=\s*\d+\.|\n\d+\.)", spoken, re.DOTALL)
        intro = re.sub(r"[:.]+$", "", intro_m.group(1).strip() if intro_m else "Here are a few options").strip()
        items_str = ", ".join(clean_items[:-1]) + f", and {clean_items[-1]}"
        return f"{intro}: {items_str}. Take a look at the cards on screen to see each one!"

    return spoken


def _normalize_llm_markdown(t: str) -> str:
    if not t or ("|" not in t and " - " not in t and " #" not in t):
        return t
    t = t.replace("\r\n", "\n")
    t = re.sub(r"\|\s*\|+", "|\n|", t)
    t = re.sub(r"^([^|\n]+?)\s*(\|\s*[^|\n]+\|)", r"\1\n\n\2", t, flags=re.MULTILINE)
    t = re.sub(r"(\|\s*)(#{1,6}\s+)", r"\1\n\n\2", t)
    t = re.sub(r"(\|)\s*([A-Za-z*#][^|\n]*)$", r"\1\n\n\2", t, flags=re.MULTILINE)
    t = re.sub(r"([^\n])\s+-\s+([A-Za-z0-9*])", r"\1\n- \2", t)
    t = re.sub(r"([^\n])\s+(\d+\.)\s+([A-Za-z0-9*])", r"\1\n\2 \3", t)
    return t


# ═══════════════════════════════════════════════════════════════
#  Unified Agentic Query Processor (Zero Duplicate Loops)
# ═══════════════════════════════════════════════════════════════

def _process_query(
    query: str,
    is_voice: bool = True,
    request_id: str | None = None,
    display_prompt: str | None = None,
    attached_context: str = "",
) -> dict:
    """
    Core query processor:
    1. Routes using the clean user query + multi-turn conversation history.
    2. Injects any attached file/image context cleanly into tool execution without polluting router JSON.
    3. Delegates execution and chaining to `tool_registry.execute_actions`.
    """
    set_assistant_state("processing")
    clean_user_query = (display_prompt or query).strip()
    history = rag_engine.get_recent_conversations(6 if is_voice else 10)

    # Only check clipboard when the user's query actually mentions clipboard keywords
    q_low = clean_user_query.lower()
    clipboard_used = any(kw in q_low for kw in _CLIPBOARD_KEYWORDS) and bool(get_clipboard_text())

    t0 = time.perf_counter()
    if attached_context:
        # When user explicitly attaches a file in the UI, route directly to chat with that context
        actions = [{"tool": "chat", "params": {"doc_context": attached_context}, "speak": ""}]
    else:
        actions = get_agent_action(clean_user_query, conversation_history=history)

    logger.debug("[Timing] request_id=%s stage=route duration_ms=%.1f", request_id, (time.perf_counter() - t0) * 1000)

    # Broadcast primary detected intent with descriptive labels for UI
    first_tool = actions[0].get("tool", "chat") if actions else "chat"
    first_params = actions[0].get("params", {}) if actions else {}
    _tool_label_map = {
        "web_search": ("Searching Web", str(first_params.get("query") or clean_user_query)),
        "open_website": ("Browsing", str(first_params.get("title") or first_params.get("url") or "Web")),
        "open_app": ("Opening App", str(first_params.get("name") or clean_user_query)),
        "close_app": ("Closing App", str(first_params.get("name") or first_params.get("app_name") or clean_user_query)),
        "open_file": ("Opening File", str(first_params.get("path") or "Document")),
        "document_qa": ("Analyzing Documents", str(first_params.get("filepath") or first_params.get("query") or "Indexed files")),
        "ask_document": ("Analyzing Documents", str(first_params.get("filepath") or first_params.get("query") or "Indexed files")),
        "summarize_document": ("Summarizing", str(first_params.get("filepath") or "Document")),
        "stock_quote": ("Market Quote", str(first_params.get("symbol") or "Price quote").upper()),
        "get_weather": ("Checking Weather", str(first_params.get("city") or "Local forecast")),
        "play_youtube": ("Streaming Media", str(first_params.get("query") or "YouTube")),
        "generate_content": ("Drafting Content", str(first_params.get("topic") or first_params.get("type") or "Draft")),
        "find_file": ("Finding File", str(first_params.get("query") or "File system")),
        "read_screen": ("Inspecting Screen", "Visual screen analysis"),
        "set_timer": ("Setting Timer", f"{first_params.get('duration', '')}s countdown"),
        "set_reminder": ("Setting Reminder", str(first_params.get("query") or "Reminder")),
        "chat": ("Thinking", clean_user_query[:35] if clean_user_query else ""),
    }
    status_label, status_detail = _tool_label_map.get(first_tool.lower(), (first_tool.replace("_", " ").title(), ""))
    broadcaster.broadcast("intent_detected", {
        "intent": first_tool.lower(),
        "tool": first_tool.lower(),
        "params": first_params,
        "label": status_label,
        "detail": status_detail,
    })

    # Delegate execution & tool chaining to tool_registry.execute_actions
    exec_results = execute_actions(actions, query=clean_user_query, conversation_history=history)

    combined_spoken: list[str] = []
    last_tool = first_tool
    last_params = first_params
    last_remember = ""
    last_url = None
    response_metadata: dict[str, object] = {}

    for action, (res_spoken, res_url, meta) in zip(actions, exec_results):
        tool = action.get("tool", "chat")
        params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
        speak_prefix = action.get("speak_prefix", "")
        if action.get("remember"):
            last_remember = action["remember"]
        last_tool, last_params = tool, params

        if meta:
            if response_metadata.get("showGeneratedPanel") is True:
                meta = {**meta, "showGeneratedPanel": True}
            response_metadata.update(meta)

        if res_url:
            last_url = res_url

        raw_to_speak = res_spoken or action.get("speak", "")
        final_to_speak = (speak_prefix + raw_to_speak) if raw_to_speak else speak_prefix
        if final_to_speak and final_to_speak not in combined_spoken:
            combined_spoken.append(final_to_speak)

    primary_tool = "multi_command" if len(actions) > 1 else last_tool
    final_reply = "\n\n".join(combined_spoken).strip()
    if final_reply:
        final_reply = _normalize_llm_markdown(final_reply)
    elif primary_tool not in _SILENT_TOOLS:
        final_reply = get_quick_feedback(f"finished {primary_tool.replace('_', ' ')}")

    # Persist turn to memory BEFORE speaking so rapid follow-up questions always see the latest turn!
    add_to_memory(
        clean_user_query,
        final_reply,
        tool=primary_tool,
        params=last_params,
        clipboard_used=clipboard_used,
        remember=last_remember,
        url=last_url,
    )

    last_thought = get_last_thought()
    if last_thought:
        response_metadata["thought"] = last_thought

    broadcaster.broadcast("chat_message", {
        "sender": "assistant",
        "text": final_reply,
        "tool": primary_tool,
        "url": last_url,
        "thought": last_thought,
        "user_query": clean_user_query,
    })

    # Trigger TTS speech
    if final_reply:
        speech_to_voice = format_conversational_speech(final_reply)
        speak(speech_to_voice, request_id=request_id)

    return {
        "tool": primary_tool,
        "params": last_params,
        "response": final_reply,
        "url": last_url,
        "metadata": response_metadata,
        "thought": last_thought,
    }


def process_query(
    query: str,
    is_voice: bool = True,
    display_prompt: str | None = None,
    attached_context: str = "",
) -> dict:
    request_id = uuid.uuid4().hex[:12]
    request_started = time.perf_counter()
    try:
        result = _process_query(
            query,
            is_voice=is_voice,
            request_id=request_id,
            display_prompt=display_prompt,
            attached_context=attached_context,
        )
        meta = result.setdefault("metadata", {})
        meta["request_id"] = request_id
        meta["duration_ms"] = round((time.perf_counter() - request_started) * 1000, 1)
        if result.get("tool") == "clarification" or meta.get("requires_clarification"):
            meta["status"] = "requires_clarification"
        elif not meta.get("status"):
            meta["status"] = "completed"
        return result
    except Exception as exc:
        logger.exception("[Query Error] %s", exc)
        error_reply = "I couldn't complete that request. Please try again."
        broadcaster.broadcast("chat_message", {"sender": "assistant", "text": error_reply, "tool": "error"})
        return {"tool": "error", "params": {}, "response": error_reply, "url": None, "metadata": {"status": "failed"}}
    finally:
        set_assistant_state("idle")


# ═══════════════════════════════════════════════════════════════
#  Flask HTTP Routes & API
# ═══════════════════════════════════════════════════════════════

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


_UI_DIST_DIR = os.path.join(_PROJECT_ROOT, "ui_app", "dist")
if not os.path.exists(_UI_DIST_DIR):
    _UI_DIST_DIR = os.path.join(os.path.dirname(__file__), "ui_app", "dist")


@app.route("/")
def index():
    dist_index = os.path.join(_UI_DIST_DIR, "index.html")
    if os.path.exists(dist_index):
        with open(dist_index, "r", encoding="utf-8") as f:
            return f.read()
    info = get_active_model_info()
    return jsonify({
        "status": "online",
        "service": "Amigo AI Engine & Windows 11 Voice Assistant Backend",
        "model": info.get("name", "Spark-X2.5 4B"),
    })


@app.route("/assets/<path:path>")
def serve_assets(path):
    return send_from_directory(os.path.join(_UI_DIST_DIR, "assets"), path)


@app.route("/favicon.ico")
def favicon():
    fav = os.path.join(_UI_DIST_DIR, "favicon.ico")
    return send_from_directory(_UI_DIST_DIR, "favicon.ico") if os.path.exists(fav) else Response(status=204)


UPLOAD_DIR = os.environ.get("AMIGO_UPLOAD_DIR", os.path.join(_PROJECT_ROOT, "rag_data", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".svg", ".ico"}


@app.route("/api/upload", methods=["POST", "OPTIONS"])
def api_upload():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename, saved_path = None, None

    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        raw_filename = os.path.basename(f.filename)
        safe_name = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)}"
        saved_path = os.path.join(UPLOAD_DIR, safe_name)
        f.save(saved_path)
        filename = raw_filename
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        raw_filename = os.path.basename(data.get("filename", "upload.bin"))
        b64_data = data.get("fileData", "")
        if b64_data:
            if "," in b64_data:
                b64_data = b64_data.split(",", 1)[1]
            try:
                safe_name = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)}"
                saved_path = os.path.join(UPLOAD_DIR, safe_name)
                with open(saved_path, "wb") as wf:
                    wf.write(base64.b64decode(b64_data))
                filename = raw_filename
            except Exception as be:
                logger.error("[Upload] Base64 decode error: %s", be)

    if not saved_path or not os.path.exists(saved_path):
        return jsonify({"success": False, "error": "No file uploaded or invalid file data"}), 400

    ext = os.path.splitext(filename or saved_path)[1].lower()
    file_size = os.path.getsize(saved_path)
    file_type = "pdf" if ext == ".pdf" else ("image" if ext in IMAGE_EXTENSIONS else ("document" if ext in rag_engine.SUPPORTED_EXTENSIONS else "generic"))

    extracted_preview = ""
    if file_type in ("pdf", "document"):
        try:
            extracted_text = rag_engine.extract_text(saved_path)
            extracted_preview = (extracted_text or "").strip()[:800]
            rag_engine.index_document(saved_path)
            rag_engine.update_active_state("active_file", {"path": saved_path, "name": filename, "type": file_type})
        except Exception as e:
            logger.error("[Upload] Error indexing %s: %s", saved_path, e)
    elif file_type == "image":
        try:
            from PIL import Image
            from amigo.services.screen_vision import analyze_image, read_text_from_image
            with Image.open(saved_path) as img:
                ocr_text = read_text_from_image(img) or ""
            visual_desc = analyze_image(saved_path, question="Summarize and describe what is visible in this image.")
            extracted_preview = visual_desc or ocr_text.strip()[:800]
            rag_engine.update_active_state("active_file", {
                "path": saved_path, "name": filename, "type": "image",
                "description": visual_desc, "ocr_text": ocr_text.strip()[:1000],
            })
        except Exception:
            pass

    return jsonify({
        "success": True,
        "filename": filename,
        "filepath": saved_path,
        "fileType": file_type,
        "size": file_size,
        "extractedPreview": extracted_preview,
        "message": f"Successfully uploaded and indexed {filename}",
    })


@app.route("/api/assistant/process", methods=["POST", "OPTIONS"])
def api_assistant_process():
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
            "model": info.get("name", "Spark-X2.5 4B"),
            "version": "Windows 11 Voice Assistant",
        })

    prompt = str(data.get("prompt") or "").strip()
    attachment = data.get("attachment")
    is_voice_req = bool(data.get("isVoice", True))

    if not prompt:
        if isinstance(attachment, dict) and attachment.get("filename"):
            prompt = f"Summarize and analyze {attachment.get('filename')}"
        else:
            return jsonify({"error": "Empty prompt"}), 400

    attached_file_context = ""
    if isinstance(attachment, dict):
        att_filename = attachment.get("filename", "")
        att_path = attachment.get("filepath", "")
        att_type = attachment.get("fileType", "")
        att_preview = attachment.get("extractedPreview", "")
        if att_path and os.path.exists(att_path):
            rag_engine.update_active_state("active_file", {"path": att_path, "name": att_filename})
            if att_type in ("pdf", "document"):
                doc_ctx = rag_engine.build_file_context(att_path, prompt) or rag_engine.extract_text(att_path)[:3000]
                attached_file_context = f"[Attached Document: {att_filename}]\n{doc_ctx}"
            elif att_type == "image":
                attached_file_context = f"[Attached Image: {att_filename}]\nExtracted Visual OCR Text:\n{att_preview}"
        elif att_preview:
            attached_file_context = f"[Attached File: {att_filename}]\nContent:\n{att_preview}"

    broadcaster.broadcast("chat_message", {"sender": "user", "text": prompt})

    result = process_query(
        prompt,
        is_voice=is_voice_req,
        display_prompt=prompt,
        attached_context=attached_file_context,
    )
    tool = result.get("tool", "chat")
    params = result.get("params", {})
    response_text = result.get("response", "")
    url = result.get("url", "")
    result_metadata = result.get("metadata", {})

    action_cards = [] if attachment else build_action_cards(
        tool=tool, params=params, result_metadata=result_metadata,
        url=url, response_text=response_text, prompt=prompt,
    )

    has_multiple_cards = bool(
        action_cards and (
            tool == "clarification"
            or result_metadata.get("requires_clarification")
            or (len(action_cards) > 1 and any(not c.get("selected") for c in action_cards))
        )
    )

    speech_reply = response_text or (get_quick_feedback(f"starting {tool.replace('_', ' ')}") if tool not in _SILENT_TOOLS else "")
    if "matching file" in speech_reply and "\n" in speech_reply:
        m_count = _RE_FILE_MATCH_COUNT.search(speech_reply)
        c_num = m_count.group(1) if m_count else "some"
        speech_reply = f"I found {c_num} matching files. Choose one on your screen or say 'Open number 1'."

    status = result_metadata.get("status") or "completed"
    headlines = {
        "requires_clarification": "Clarification Needed",
        "offline": "Offline",
        "failed": "Action Failed",
    }
    headline = headlines.get(status, "Action Completed")

    return jsonify({
        "speechReply": speech_reply,
        "displayTitle": prompt,
        "intent": tool,
        "params": params,
        "requiresDisambiguation": has_multiple_cards,
        "actionCards": action_cards,
        "contactMatches": [],
        "executionSummary": {
            "status": status,
            "headline": headline,
            "details": response_text or f"{headline}: {tool.replace('_', ' ')}.",
            "secondaryDetails": url or "",
        },
        "metadata": {**result_metadata, "params": params},
        "generatedContent": result_metadata.get("generated_content"),
        "contentType": result_metadata.get("content_type"),
        "contentTopic": result_metadata.get("topic"),
        "showGeneratedPanel": result_metadata.get("showGeneratedPanel", False),
        "url": url,
    })


@app.route("/api/assistant/process/action", methods=["POST", "OPTIONS"])
@app.route("/api/action/execute", methods=["POST", "OPTIONS"])
def api_action_execute():
    """Execute action card callback AND record it in conversation memory for follow-ups."""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.get_json() or {}
    payload = data.get("payload") or {}
    tool = payload.get("tool") or data.get("type", "chat")
    if tool in UI_TOOL_HANDLERS:
        try:
            history = rag_engine.get_recent_conversations(6)
            query_text = data.get("title") or payload.get("query") or payload.get("path") or tool
            spoken, url, metadata = execute_tool(
                tool,
                payload,
                query=str(query_text),
                conversation_history=history,
            )

            orig_prompt = payload.get("original_prompt") or payload.get("query")
            if orig_prompt:
                rag_engine.add_user_fact(
                    f"User clarification: For request '{orig_prompt}', user chose tool '{tool}'",
                    category="user_correction",
                )

            # Record action in conversation history so follow-up questions know what was opened/run
            add_to_memory(str(query_text), spoken or f"Executed {tool}", tool=tool, params=payload)

            if spoken:
                speak(spoken)
                broadcaster.broadcast("chat_message", {
                    "sender": "assistant",
                    "text": spoken,
                    "tool": tool,
                    "url": url,
                })

            success = (metadata or {}).get("status") not in {"blocked", "failed"}
            return jsonify({"success": success, "message": spoken or "Executed", "url": url, "metadata": metadata or {}}), (403 if not success else 200)
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
            return jsonify({"transcription": transcribe_b64(audio_b64)})
        except Exception as e:
            return jsonify({"transcription": "", "error": str(e)}), 500
    return jsonify({"transcription": data.get("text", "")})


@app.route("/api/speak", methods=["POST", "OPTIONS"])
def api_speak():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "Empty text"}), 400
    speak(text, voice=data.get("voice"))
    return jsonify({"success": True})


@app.route("/api/stop-speaking", methods=["POST", "OPTIONS"])
def api_stop_speaking():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    stop_speaking()
    return jsonify({"success": True})


@app.route("/events")
def events():
    def stream():
        q = broadcaster.subscribe()
        try:
            yield "data: " + json.dumps({"type": "state_change", "state": current_state}) + "\n\n"
            while True:
                try:
                    yield q.get(timeout=15.0)
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            broadcaster.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/status")
def get_status():
    return jsonify({"state": current_state})


@app.route("/api/system-stats")
def get_system_stats():
    """0ms non-blocking system stats using cached network check and interval=None."""
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        _, online = _get_internet_status()
        return jsonify({
            "cpu": round(cpu, 1),
            "ram": round(mem.percent, 1),
            "battery": round(battery.percent, 1) if battery else None,
            "plugged": battery.power_plugged if battery else None,
            "online": online,
        })
    except Exception:
        return jsonify({"cpu": 0, "ram": 0, "battery": None, "plugged": None, "online": False})


@app.route("/api/internal/broadcast", methods=["POST", "OPTIONS"])
def api_internal_broadcast():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "chat_message")
    broadcaster.broadcast(event_type, payload.get("data", {}))
    return jsonify({"status": "ok", "broadcasted": event_type})


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
    return jsonify(process_query(user_query, is_voice=bool(data.get("is_voice", False))))


@app.route("/api/quick-action", methods=["POST"])
def handle_quick_action():
    data = request.get_json() or {}
    mapping = {
        "time": "what time is it",
        "screenshot": "take a screenshot",
        "weather": "check current weather",
        "favorites": "play my favorite songs",
        "mute": "mute audio",
        "lock": "lock my pc",
        "sleep": "put pc to sleep",
        "empty_bin": "empty recycle bin",
        "status": "check system status",
    }
    query = mapping.get(data.get("action", ""), "what time is it")
    return jsonify(process_query(query, is_voice=False))


@app.route("/api/clear-memory", methods=["POST", "OPTIONS"])
@app.route("/api/history/clear", methods=["POST", "OPTIONS"])
@app.route("/api/memory/clear", methods=["POST", "OPTIONS"])
def clear_memory():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    try:
        data = request.get_json(silent=True) or {}
        clear_profile = bool(data.get("clear_profile", False))
        clear_facts = bool(data.get("clear_facts", False))
        clear_conversations_memory(clear_profile=clear_profile)
        if clear_facts and not clear_profile:
            rag_engine.clear_user_facts()
        broadcaster.broadcast("history_cleared", {})
        return jsonify({"success": True, "message": "Memory cleared"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def get_current_user_name() -> str:
    try:
        memory = load_memory()
        name = memory.get("user_profile", {}).get("identity", {}).get("name") or memory.get("identity", {}).get("name")
        if name and str(name).strip():
            return str(name).strip()
    except Exception:
        pass
    uname = os.getenv("USERNAME") or os.getenv("USER") or ""
    return uname.strip().capitalize()


@app.route("/api/settings", methods=["GET", "POST", "OPTIONS"])
def handle_settings():
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
            "theme", "autoSpeech", "model", "thinkingEnabled", "voice",
        ):
            if k in data:
                ui_settings[k] = data[k]

        thinking_val = data.get("thinkingEnabled")
        if thinking_val is None and isinstance(data.get("backendConfig"), dict):
            thinking_val = data["backendConfig"].get("thinkingEnabled")
        if thinking_val is not None:
            set_thinking_enabled(bool(thinking_val))
            ui_settings["thinkingEnabled"] = bool(thinking_val)

        if isinstance(data.get("user_profile"), dict):
            memory["user_profile"] = data["user_profile"]

        target_voice = (
            data.get("voice")
            or data.get("user_profile", {}).get("preferences", {}).get("voice")
            or data.get("ui_settings", {}).get("voice")
        )
        if target_voice and isinstance(target_voice, str):
            _apply_voice_preference(memory, target_voice)

        if "theme" in data:
            memory.setdefault("user_profile", {}).setdefault("preferences", {})["theme"] = data["theme"]
        if "model" in data:
            set_active_model(data["model"])

        save_memory(memory)
        broadcaster.broadcast("settings_updated", data)
        return jsonify({"success": True, "ui_settings": ui_settings, "active_voice": get_active_voice()})

    info = get_active_model_info()
    curr_voice = get_active_voice()
    ui_settings_resp = memory.get("ui_settings", {})
    ui_settings_resp["voice"] = curr_voice
    u_prof = memory.get("user_profile", {})
    u_prof.setdefault("preferences", {})["voice"] = curr_voice
    return jsonify({
        "ui_settings": ui_settings_resp,
        "user_profile": u_prof,
        "user_name": get_current_user_name(),
        "voice": curr_voice,
        "model": info.get("key", "spark-x2.5-4b"),
        "model_name": info.get("name", "Spark-X2.5 4B"),
        "thinking_enabled": is_thinking_enabled(),
        "available_models": get_available_models(),
        "available_voices": get_voice_catalog(),
    })


@app.route("/api/user-profile", methods=["GET", "POST", "OPTIONS"])
def handle_user_profile():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    memory = load_memory()
    if request.method == "POST":
        data = request.get_json() or {}
        user_prof = memory.setdefault("user_profile", {})
        if isinstance(data.get("identity"), dict):
            user_prof.setdefault("identity", {}).update(data["identity"])
        if isinstance(data.get("custom_facts"), list):
            user_prof["custom_facts"] = data["custom_facts"]
        if "name" in data:
            user_prof.setdefault("identity", {})["name"] = data["name"]
        if "role" in data:
            user_prof.setdefault("identity", {})["role"] = data["role"]
        if isinstance(data.get("preferences"), dict):
            user_prof.setdefault("preferences", {}).update(data["preferences"])
            if v_target := data["preferences"].get("voice"):
                _apply_voice_preference(memory, str(v_target))
        save_memory(memory)
        return jsonify({"success": True, "user_profile": memory.get("user_profile", {})})

    return jsonify(memory.get("user_profile", {}))


@app.route("/api/voices", methods=["GET", "POST", "OPTIONS"])
def manage_voices():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    if request.method == "POST":
        data = request.get_json() or {}
        v_key = data.get("voice") or data.get("id") or data.get("voice_id")
        if v_key and isinstance(v_key, str):
            memory = load_memory()
            v_name = _apply_voice_preference(memory, v_key)
            save_memory(memory)
            broadcaster.broadcast("voice_change", {"voice": v_key.lower(), "name": v_name})
            return jsonify({"success": True, "active_voice": v_key.lower(), "name": v_name})
        return jsonify({"error": "No voice specified"}), 400

    return jsonify({"active_voice": get_active_voice(), "catalog": get_voice_catalog()})


@app.route("/api/health", methods=["GET"])
def handle_health():
    return jsonify({"status": "healthy", "engine": "Amigo AI", "tts": get_tts_engine_name(), "timestamp": time.time()})


@app.route("/api/shutdown", methods=["POST"])
def shutdown_server():
    threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
    return jsonify({"status": "shutting_down"})


@app.route("/api/weather")
def get_weather_endpoint():
    return jsonify(get_weather_data(request.args.get("city", "").strip()))


@app.route("/api/stock")
def get_stock_endpoint():
    from amigo.services.web_search import get_stock_quote
    symbol = request.args.get("symbol", "").strip()
    range_val = request.args.get("range", "1d").strip()
    if not symbol:
        return jsonify({"error": "Symbol is required"}), 400
    quote = get_stock_quote(symbol, range_val=range_val)
    if not quote:
        return jsonify({"error": f"Could not fetch quote for {symbol}"}), 404
    return jsonify({
        "symbol": quote.symbol, "name": quote.name, "price": quote.price,
        "currency": quote.currency, "previous_close": quote.previous_close,
        "change": quote.change, "change_pct": quote.change_pct, "exchange": quote.exchange,
        "day_high": quote.day_high, "day_low": quote.day_low, "volume": quote.volume,
        "fifty_two_week_high": quote.fifty_two_week_high, "fifty_two_week_low": quote.fifty_two_week_low,
        "history": quote.history, "timestamps": quote.timestamps,
        "as_of": quote.as_of.strftime("%b %d, %I:%M %p") if quote.as_of else "", "url": quote.url,
    })


@app.route("/api/media/status")
def get_media_status():
    return jsonify(_current_media)


@app.route("/api/media/control", methods=["POST"])
def handle_media_control():
    data = request.get_json() or {}
    action = data.get("action", "")
    if action == "play_pause":
        play_pause_media()
        _current_media["status"] = "paused" if _current_media.get("status") == "playing" else "playing"
        broadcaster.broadcast("media_update", _current_media)
        return jsonify({"success": True, "status": _current_media["status"]})
    if action == "next":
        next_track()
        return jsonify({"success": True})
    if action == "prev":
        prev_track()
        return jsonify({"success": True})
    if action == "volume":
        vol = int(data.get("level", 50))
        set_volume(vol)
        _current_media["volume"] = vol
        broadcaster.broadcast("media_update", _current_media)
        return jsonify({"success": True, "volume": vol})
    if action == "play_query":
        if q := data.get("query", "").strip():
            execute_tool("play_youtube", {"query": q}, query=q, spoken=f"Playing {q}")
        return jsonify({"success": True})
    return jsonify({"error": "Unknown action"}), 400


@app.route("/api/models", methods=["GET", "POST"])
def manage_models():
    if request.method == "POST":
        model_key = (request.get_json() or {}).get("model", "")
        if model_key:
            set_active_model(model_key)
            info = get_active_model_info()
            broadcaster.broadcast("model_change", info)
            return jsonify({"success": True, "active_model": info})
        return jsonify({"error": "No model specified"}), 400
    return jsonify({"active": get_active_model_info(), "available": get_available_models()})


@app.route("/api/listen", methods=["POST"])
def trigger_listen():
    broadcaster.broadcast("state_change", {"state": "listening"})
    return jsonify({"status": "listening"})


@app.route("/api/reminders", methods=["GET", "POST", "DELETE"])
def manage_reminders():
    if request.method == "POST":
        data = request.get_json() or {}
        res = handle_set_timer(data, data.get("query", "")) if "duration" in data else handle_set_reminder(data, data.get("query", ""))
        return jsonify({"success": True, "message": res})
    if request.method == "DELETE":
        res = handle_cancel_reminder(request.get_json(silent=True) or {})
        return jsonify({"success": True, "message": res, "active": get_active_data()})
    return jsonify(get_active_data())


@app.route("/api/active-state", methods=["GET"])
def get_state_endpoint():
    return jsonify(get_active_state(clean_expired=True))


# ── RAG, Email, Calendar & Proactive Endpoints ─────────────────

@app.route("/api/rag/status")
def rag_status_endpoint():
    stats = rag_engine.get_index_stats()
    try:
        idx_status = get_indexer(rag_engine).get_status()
        stats["indexer"] = idx_status
        stats["is_indexing"] = idx_status.get("is_indexing", False)
    except Exception:
        pass
    return jsonify(stats)


@app.route("/api/rag/search", methods=["POST"])
def rag_search_endpoint():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400
    results = rag_engine.search(
        query,
        target_collections=data.get("collections"),
        top_k=int(data.get("top_k", 5)),
        query_type=data.get("query_type", "auto"),
        metadata_filter=data.get("metadata_filter"),
    )
    return jsonify({"query": query, "results": results})


@app.route("/api/rag/debug-search", methods=["POST"])
def rag_debug_search_endpoint():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400
    return jsonify(rag_engine.debug_search(query, target_collections=data.get("collections"), top_k=int(data.get("top_k", 10))))


@app.route("/api/rag/reinitialize-reranker", methods=["POST"])
def rag_reinitialize_reranker():
    success = rag_engine.reinitialize_reranker()
    return jsonify({"success": success})


@app.route("/api/rag/reindex", methods=["POST"])
def rag_reindex_endpoint():
    try:
        force = (request.get_json(silent=True) or {}).get("force", True)
        indexer = get_indexer(rag_engine)

        def _progress_cb(processed, total, indexed, skipped, fname):
            broadcaster.broadcast("rag_indexing_progress", {
                "is_indexing": True, "files_total": total, "files_processed": processed,
                "files_indexed": indexed, "files_skipped": skipped,
                "progress_percent": round((processed / max(total, 1)) * 100, 1),
                "current_file": fname, "status_message": f"Processing ({processed}/{total}): {fname}",
            })

        def _run():
            broadcaster.broadcast("rag_indexing_progress", {
                "is_indexing": True, "files_total": 0, "files_processed": 0,
                "progress_percent": 0.0, "current_file": "Scanning...", "status_message": "Scanning directories...",
            })
            indexer.full_index(force=force, progress_cb=_progress_cb)
            st = indexer.get_status()
            broadcaster.broadcast("rag_indexing_progress", {
                "is_indexing": False, **st, "progress_percent": 100.0, "current_file": "",
            })

        threading.Thread(target=_run, daemon=True, name="rag-manual-reindex").start()
        return jsonify({"success": True, "message": "Re-indexing started in background."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/emails")
def emails_endpoint():
    try:
        from amigo.services.mail_integration import get_recent_emails, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook not available"}), 503
        return jsonify({"emails": get_recent_emails(int(request.args.get("count", 5)))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/emails/unread")
def emails_unread_endpoint():
    try:
        from amigo.services.mail_integration import get_unread_count, get_unread_emails, is_outlook_available
        if not is_outlook_available():
            return jsonify({"error": "Outlook not available"}), 503
        count = get_unread_count()
        return jsonify({"unread_count": count, "emails": get_unread_emails(min(count, 10)) if count > 0 else []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/emails/search", methods=["POST"])
def emails_search_endpoint():
    try:
        from amigo.services.mail_integration import is_outlook_available, search_emails
        if not is_outlook_available():
            return jsonify({"error": "Outlook not available"}), 503
        q = (request.get_json() or {}).get("query", "").strip()
        return (jsonify({"query": q, "results": search_emails(q)}) if q else (jsonify({"error": "Empty query"}), 400))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar")
def calendar_endpoint():
    try:
        from amigo.services.calendar_integration import get_todays_events, is_outlook_available
        return (jsonify({"events": get_todays_events()}) if is_outlook_available() else (jsonify({"error": "Outlook Calendar not available"}), 503))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar/upcoming")
def calendar_upcoming_endpoint():
    try:
        from amigo.services.calendar_integration import get_upcoming_events, is_outlook_available
        return (jsonify({"events": get_upcoming_events(days=int(request.args.get("days", 7)))}) if is_outlook_available() else (jsonify({"error": "Outlook Calendar not available"}), 503))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proactive/status")
def api_proactive_status():
    try:
        return jsonify(get_proactive_intelligence().get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proactive/config", methods=["GET", "POST"])
def api_proactive_config():
    try:
        proactive = get_proactive_intelligence()
        if request.method == "POST":
            proactive.update_config(request.get_json() or {})
            return jsonify({"success": True, "config": proactive.config})
        return jsonify(proactive.config)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proactive/dismiss", methods=["POST"])
def api_proactive_dismiss():
    try:
        sid = (request.get_json() or {}).get("suggestion_id")
        if not sid:
            return jsonify({"error": "Missing suggestion_id"}), 400
        get_proactive_intelligence().dismiss_suggestion(sid)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proactive/acknowledge", methods=["POST"])
def api_proactive_acknowledge():
    try:
        sid = (request.get_json() or {}).get("suggestion_id")
        if not sid:
            return jsonify({"error": "Missing suggestion_id"}), 400
        get_proactive_intelligence().acknowledge_suggestion(sid)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proactive/trigger", methods=["POST"])
def api_proactive_trigger():
    try:
        data = request.get_json(silent=True) or {}
        level = data.get("level") or "info"
        sent = get_proactive_intelligence().trigger_notification(data.get("message"), level)
        return jsonify({"success": True, "message": sent, "level": level})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/hotkey/status")
def api_hotkey_status():
    try:
        from amigo.ui.hotkey_service import is_hotkey_service_running
        return jsonify({"hotkey": "Alt+V", "running": is_hotkey_service_running()})
    except Exception as e:
        return jsonify({"running": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  Server Lifecycle & Async Initialization
# ═══════════════════════════════════════════════════════════════

_llm_ready = threading.Event()
_llm_load_error: Optional[Exception] = None


def _cleanup_all():
    try:
        shutdown_proactive_intelligence()
    except Exception:
        pass


atexit.register(_cleanup_all)


def _load_llm_background():
    global _llm_load_error
    try:
        init_local_llm()
        _llm_ready.set()
    except Exception as e:
        _llm_load_error = e
        _llm_ready.set()
        return

    # Pre-warm both the Router KV prefix and the Voice Assistant KV prefix
    try:
        query_local_llm(
            [{"role": "system", "content": get_static_router_prefix()}, {"role": "user", "content": "hi"}],
            system_prompt="",
            max_tokens=8,
            temperature=0.0,
            thinking=False,
            sanitize=False,
            response_format={"type": "json_object"},
            stop=_ROUTER_STOP,
        )
        logger.info("[Local AI Engine] Router KV prefix cache pre-warmed.")
    except Exception:
        pass


def initialize_amigo_async():
    print("[ AMIGO ] Starting async initialization...")
    rag_engine.init_rag(background=True)
    threading.Thread(
        target=lambda: rag_engine.get_user_profile_prompt("warmup", semantic_search=True),
        daemon=True,
        name="RAGWarmup",
    ).start()
    start_background_indexer(rag_engine, interval_minutes=30)

    try:
        from amigo.services.web_search import warmup
        warmup()
    except Exception:
        pass

    try:
        from amigo.ui import hotkey_service
        hotkey_service.set_broadcast_callback(broadcaster.broadcast)
        hotkey_service.start_hotkey_service()
    except Exception:
        pass

    threading.Thread(target=_load_llm_background, daemon=True, name="LLMLoader").start()

    spinner = ["|", "/", "-", "\\"]
    i, start_time = 0, time.time()
    while not _llm_ready.is_set():
        print(f"\r[ AMIGO ] Loading AI model... {spinner[i % 4]} ({time.time() - start_time:.1f}s)", end="", flush=True)
        i += 1
        time.sleep(0.1)

    if _llm_load_error:
        print(f"\r[ AMIGO ] AI model load FAILED: {_llm_load_error}")
        raise _llm_load_error
    print(f"\r[ AMIGO ] AI model loaded in {time.time() - start_time:.1f}s OK")

    init_proactive_intelligence(speak_callback=speak, broadcast_callback=broadcaster.broadcast)
    print("[ AMIGO ] All systems ready!")


def launch_server(port: int = 5000, open_browser: bool = True) -> None:
    def sig_handler(_signum, _frame):
        _cleanup_all()
        os._exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, sig_handler)
        except Exception:
            pass

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    init_reminders(speak_callback=speak, broadcast_callback=broadcaster.broadcast)

    try:
        from werkzeug.serving import run_simple
        run_simple("0.0.0.0", port, app, use_reloader=False, threaded=True)
    except Exception:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        _cleanup_all()


if __name__ == "__main__":
    _info = get_active_model_info()
    print("==================================================")
    print("   AMIGO VOICE ASSISTANT - UI DASHBOARD SERVER   ")
    print(f"   [ {_info['name'].upper()} | {_info['tts_engine'].upper()} | RAG + AGENTIC ]")
    print("==================================================")
    initialize_amigo_async()
    launch_server(port=5000, open_browser=True)
