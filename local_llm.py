"""
Local LLM Module for Amigo Voice Assistant.
Executes Qwen 3.5 2B Instruct locally via llama-cpp-python on Windows.
"""

import datetime
import json
import logging
import multiprocessing
import os
import re
import sys
import threading
import time
import unicodedata
from typing import Any, Generator

logger = logging.getLogger("amigo.local_llm")

try:
    import pyperclip
except ImportError:
    pyperclip = None


def get_clipboard_text() -> str | None:
    """Return up to 1000 characters from system clipboard."""
    if pyperclip:
        try:
            text = pyperclip.paste()
            if text and isinstance(text, str):
                return text[:1000].strip()
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Qwen 3.5 2B Model Configuration
# ---------------------------------------------------------------------------
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "models"))
MODEL_NAME = "Qwen 3.5 2B Instruct"
MODEL_FILENAME = "qwen3.5-2b-instruct-q4_k_m.gguf"
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)

MODEL_TARGETS = [
    ("bartowski/Qwen_Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf"),
    ("unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf"),
    ("daniloreddy/Qwen3.5-2B_GGUF", "Qwen3.5-2B-Q4_K_M.gguf"),
]

STOP_TOKENS = ["<|im_end|>", "<|endoftext|>", "<|im_start|>", "User:", "Human:"]

AVAILABLE_MODELS = {
    "qwen-3.5-2b": {
        "key": "qwen-3.5-2b",
        "name": MODEL_NAME,
        "filename": MODEL_FILENAME,
        "path": MODEL_PATH,
        "targets": MODEL_TARGETS,
        "downloaded": os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 500_000_000,
        "size_gb": 1.45,
    }
}

_active_model_key = "qwen-3.5-2b"
_local_llm_instance = None
_llm_lock = threading.Lock()
_devnull = open(os.devnull, "w")


def get_available_models() -> dict:
    """Return dictionary of available models and their download status."""
    return AVAILABLE_MODELS


def set_active_model(model_key: str = "qwen-3.5-2b") -> bool:
    """Sets the active model key and reloads instance if needed."""
    global _active_model_key, _local_llm_instance
    _active_model_key = model_key
    with _llm_lock:
        _local_llm_instance = None
    init_local_llm(force_reload=True)
    return True


def get_active_model_info() -> dict:
    """Return metadata for the currently active model."""
    m_info = AVAILABLE_MODELS.get(_active_model_key, AVAILABLE_MODELS["qwen-3.5-2b"])
    m_path = m_info["path"]
    return {
        "key": _active_model_key,
        "name": m_info["name"],
        "filename": m_info["filename"],
        "path": m_path,
        "downloaded": os.path.exists(m_path) and os.path.getsize(m_path) > 500_000_000,
        "size_gb": m_info.get("size_gb", 1.45),
    }


def get_model_path() -> str:
    """Ensure active model weights exist locally; download via huggingface_hub if needed."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    active_info = AVAILABLE_MODELS.get(_active_model_key, AVAILABLE_MODELS["qwen-3.5-2b"])
    target_path = active_info["path"]
    target_name = active_info["name"]
    target_list = active_info.get("targets", MODEL_TARGETS)

    if os.path.exists(target_path) and os.path.getsize(target_path) > 500_000_000:
        return target_path

    logger.info(f"Downloading {target_name} (~{active_info.get('size_gb', 1.45)} GB)...")
    for repo, fname in target_list:
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(repo_id=repo, filename=fname, local_dir=MODEL_DIR)
            if os.path.exists(downloaded) and os.path.getsize(downloaded) > 500_000_000:
                # Normalize filename if necessary
                if downloaded != target_path and not os.path.exists(target_path):
                    try:
                        os.replace(downloaded, target_path)
                        downloaded = target_path
                    except Exception:
                        pass
                logger.info(f"Downloaded {target_name} to {downloaded}")
                return downloaded
        except Exception as e:
            logger.warning(f"Download attempt from {repo} failed: {e}")
            time.sleep(1)
    return target_path


def ensure_model_downloaded() -> str:
    """Explicit helper to trigger and verify model download."""
    return get_model_path()


def init_local_llm(force_reload: bool = False):
    """Initializes local Llama instance with GPU offloading and fast context ingestion."""
    global _local_llm_instance
    if not force_reload and _local_llm_instance is not None:
        return _local_llm_instance

    with _llm_lock:
        if not force_reload and _local_llm_instance is not None:
            return _local_llm_instance

        _local_llm_instance = None
        model_file = get_model_path()
        if not os.path.exists(model_file):
            return None

        try:
            from llama_cpp import Llama
            old_stderr = sys.stderr
            sys.stderr = _devnull
            try:
                for fa in [True, False]:
                    try:
                        _local_llm_instance = Llama(
                            model_path=model_file,
                            n_ctx=4096,
                            n_gpu_layers=-1,
                            main_gpu=0,
                            n_threads=max(1, multiprocessing.cpu_count() - 2),
                            n_batch=1024,
                            n_ubatch=512,
                            flash_attn=fa,
                            use_mmap=True,
                            verbose=False,
                        )
                        if _local_llm_instance:
                            break
                    except Exception:
                        pass
            finally:
                sys.stderr = old_stderr

            if _local_llm_instance:
                logger.info(f"[Local AI Engine] {MODEL_NAME} loaded.")
                return _local_llm_instance
        except Exception as e:
            logger.error(f"[Local AI Engine] Load error: {e}")
    return None


# ---------------------------------------------------------------------------
# TTS Text Cleaning Utilities
# ---------------------------------------------------------------------------

def strip_markdown_for_tts(text: str) -> str:
    """Strips markdown and formatting characters so TTS sounds natural."""
    if not text:
        return ""
    text = text.replace("₹", " rupees ").replace("$", " dollars ").replace("€", " euros ").replace("£", " pounds ")
    text = re.sub(r"```[a-zA-Z]*\n?([\s\S]*?)```", r"\1", text)
    text = re.sub(r"[*_~`#>]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[{}\[\]\\<>]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_tts_text(text: str) -> str:
    """Preserves spoken characters, punctuation, and Unicode letters while dropping noise symbols."""
    if not text:
        return ""
    result = [ch for ch in text if unicodedata.category(ch).startswith(("L", "N", "P", "Z", "M")) or ch in " ,.!?:;-'\"]"]
    return "".join(result).strip()


def sanitize_for_tts(text: str) -> str:
    """Combined markdown cleaner and TTS text sanitizer."""
    return clean_tts_text(strip_markdown_for_tts(text))


# ---------------------------------------------------------------------------
# LLM Generation & Real-Time Streaming
# ---------------------------------------------------------------------------

def _prepare_chat_messages(system_prompt: str, prompt: str | list[dict]) -> list[dict]:
    if isinstance(prompt, list):
        if prompt and prompt[0].get("role") == "system":
            return prompt
        return [{"role": "system", "content": system_prompt}] + prompt
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": str(prompt)}]


def query_local_llm(
    prompt: str | list[dict],
    system_prompt: str = "You are Amigo, a helpful voice assistant. Speak in clear, plain sentences.",
    max_tokens: int = 512,
) -> str:
    """Queries local LLM and returns clean spoken output."""
    llm = init_local_llm()
    if llm:
        try:
            messages = _prepare_chat_messages(system_prompt, prompt)
            res = llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0.7, stop=STOP_TOKENS)
            output = res["choices"][0]["message"]["content"]
            for tok in STOP_TOKENS:
                output = output.split(tok)[0]
            return sanitize_for_tts(output.strip()) or output.strip()
        except Exception as e:
            logger.error(f"[LLM Query Error]: {e}")
    return "I am here and ready to help."


def query_local_llm_stream(
    prompt: str | list[dict],
    system_prompt: str = "You are Amigo, a helpful voice assistant. Speak in clear, plain sentences.",
    max_tokens: int = 512,
    interruption_event: threading.Event | None = None,
) -> Generator[str, None, None]:
    """Streams raw tokens in real-time from the local LLM."""
    llm = init_local_llm()
    if llm:
        try:
            messages = _prepare_chat_messages(system_prompt, prompt)
            stream_res = llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0.7, stop=STOP_TOKENS, stream=True)
            for chunk in stream_res:
                if interruption_event and interruption_event.is_set():
                    return
                token = chunk["choices"][0].get("delta", {}).get("content", "")
                if token:
                    yield token
            return
        except Exception as e:
            logger.error(f"[LLM Stream Error]: {e}")
    yield "I am here and ready to help."


def stream_sentence_chunks(token_generator, interruption_event: threading.Event | None = None) -> Generator[str, None, None]:
    """Buffers token stream and yields complete sentence chunks immediately for TTS."""
    buffer = ""
    sentence_split = re.compile(r'(?<=[.!?])\s+|\n+')

    for token in token_generator:
        if interruption_event and interruption_event.is_set():
            return
        buffer += token
        for tok in STOP_TOKENS:
            if tok in buffer:
                buffer = buffer.split(tok)[0]

        splits = sentence_split.split(buffer)
        if len(splits) > 1:
            for s in splits[:-1]:
                cand = s.strip()
                if cand and not re.search(r'\b(mr|mrs|ms|dr|vs|eg|ie|etc)\.$', cand, re.I):
                    clean = sanitize_for_tts(cand)
                    if clean:
                        yield clean
            buffer = splits[-1]

    if buffer.strip() and not (interruption_event and interruption_event.is_set()):
        clean = sanitize_for_tts(buffer.strip())
        if clean:
            yield clean


# ---------------------------------------------------------------------------
# Tier 1: Fast-Path Regex Router (High-frequency, exact shortcuts only)
# ---------------------------------------------------------------------------


_RE_PROBE_GUARD = re.compile(
    r"\b(what (tools?|functions?|capabilities) do you have|list your tools|how were you built|"
    r"what model are you|what is your prompt|output your json|tell me a secret)\b",
    re.I
)

# Pre-compiled exact patterns for Tier 1
_RE_VOL_LEVEL = re.compile(r"\bvolume\s+(?:up\s+to\s+|down\s+to\s+|to\s+|at\s+)?(\d{1,3})\s*%?", re.IGNORECASE)
_RE_BRIGHT_LEVEL = re.compile(r"^(?:set\s+)?brightness\s+(?:to\s+)?(\d{1,3})(?:\s*%)?$", re.IGNORECASE)
_RE_TIMER_SIMPLE = re.compile(r"^(?:set\s+(?:a\s+)?)?timer\s+(?:for\s+)?(\d+)\s*(mins?|minutes?|secs?|seconds?|hrs?|hours?)$", re.IGNORECASE)
_RE_OPEN_FOLLOWUP = re.compile(r"^(?:open|show|display|view|launch)\s+(?:that|it|this|them|those|these|the\s+file|that\s+file|the\s+document|that\s+document|the\s+first\s+one|first\s+one|the\s+second\s+one|the\s+third\s+one|first|second|third|fourth|fifth|number\s+\d+|\d+)$", re.IGNORECASE)



def clean_spoken_query(query: str) -> str:
    """Normalizes spoken query by stripping conversational filler prefixes."""
    q = (query or "").lower().strip()
    q = re.sub(
        r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?(?:could\s+you\s+|can\s+you\s+|would\s+you\s+)?(?:please\s+)?(?:i\s+want\s+to\s+|tell\s+me\s+)?",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    q = re.sub(r"\s+(?:please|for\s+me|boss|amigo)$", "", q, flags=re.IGNORECASE).strip()
    return q.rstrip(".!?,;:")


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    """
    Tier 1: Ultra-fast (<0.1ms) matcher for exact, unambiguous shortcuts.
    Returns None immediately if the command requires natural language understanding.
    """
    text = clean_spoken_query(query)
    if not text:
        return {"tool": "chat", "params": {}, "speak": ""}

    # 1. Exact System & Power Actions
    if text in ("exit", "quit", "goodbye", "bye", "close amigo", "shutdown pc"):
        return {"tool": "exit", "params": {}, "speak": "Goodbye!"}
    if text in ("stop", "shut up", "be quiet", "stop talking", "stop speaking", "quiet", "silence"):
        return {"tool": "stop", "params": {}, "speak": "Stopped."}
    if text in ("lock pc", "lock my pc", "lock computer", "lock screen", "lock workstation"):
        return {"tool": "lock_pc", "params": {}, "speak": "Locking your PC."}
    if text in ("sleep pc", "sleep computer", "put pc to sleep", "sleep"):
        return {"tool": "sleep_pc", "params": {}, "speak": "Putting system to sleep."}
    if text in ("cancel shutdown", "cancel restart"):
        return {"tool": "cancel_shutdown", "params": {}, "speak": "Shutdown cancelled."}
    if text in ("empty recycle bin", "empty trash", "clear recycle bin", "clear trash"):
        return {"tool": "empty_recycle_bin", "params": {}, "speak": "Recycle bin emptied."}
    if text in ("cancel reminder", "cancel reminders", "cancel my reminders", "cancel timers"):
        return {"tool": "cancel_reminder", "params": {}, "speak": ""}


    # 2. Exact Volume / Brightness levels (prioritize explicit percentage)
    if m := _RE_VOL_LEVEL.search(text):
        return {"tool": "set_volume", "params": {"level": m.group(1)}, "speak": f"Setting volume to {m.group(1)} percent."}
    if m := _RE_BRIGHT_LEVEL.match(text):
        return {"tool": "set_brightness", "params": {"level": m.group(1)}, "speak": f"Setting brightness to {m.group(1)} percent."}

    # 3. Exact Media & Audio Shortcuts
    if text in ("volume up", "increase volume", "louder"):
        return {"tool": "volume_up", "params": {}, "speak": "Volume increased."}
    if text in ("volume down", "decrease volume", "quieter"):
        return {"tool": "volume_down", "params": {}, "speak": "Volume decreased."}
    if text in ("mute", "unmute", "silence", "mute audio", "unmute audio"):
        return {"tool": "mute", "params": {}, "speak": "Audio toggled."}
    if text in ("next track", "next song", "skip song", "skip track", "next"):
        return {"tool": "next_track", "params": {}, "speak": "Next track."}
    if text in ("previous track", "prev track", "prev song", "previous song", "previous"):
        return {"tool": "prev_track", "params": {}, "speak": "Previous track."}
    if text in ("pause", "pause music", "pause playback", "stop music", "stop playback"):
        return {"tool": "pause_media", "params": {}, "speak": "Media paused."}
    if text in ("resume", "unpause", "continue music", "play music", "resume playback"):
        return {"tool": "play_media", "params": {}, "speak": "Media resumed."}


    # 4. Exact Hardware Metrics
    if text in ("system status", "cpu usage", "ram usage", "memory usage", "battery status", "hardware metrics"):
        return {"tool": "system_status", "params": {}, "speak": "Checking system status."}

    # 5. Exact Calendar & Email Shortcuts
    if text in ("today's calendar", "today's schedule", "my calendar", "my schedule", "what's on my calendar today"):
        return {"tool": "get_calendar", "params": {"days": 1}, "speak": "Checking today's schedule."}
    if text in ("unread emails", "check unread emails", "any unread emails", "new emails", "check new emails"):
        return {"tool": "unread_emails", "params": {}, "speak": "Checking unread emails."}

    # 6. Exact Timer / Stopwatch Shortcuts
    if text in ("stopwatch", "start stopwatch", "open stopwatch"):
        return {"tool": "stopwatch", "params": {"mode": "stopwatch"}, "speak": "Starting stopwatch."}
    if m := _RE_TIMER_SIMPLE.match(text):
        qty = int(m.group(1))
        unit = m.group(2).lower()
        secs = qty * 3600 if "h" in unit else qty * 60 if "m" in unit else qty
        return {"tool": "set_timer", "params": {"duration": secs, "seconds": secs}, "speak": f"Setting a {qty} {unit} timer."}

    # 7. Exact System Folder & Explorer Shortcuts

    if text in ("windows explorer", "file explorer", "explorer", "open windows explorer", "open file explorer", "open explorer", "open this pc", "this pc", "my computer"):
        return {"tool": "open_folder", "params": {"name": "explorer"}, "speak": "Opening File Explorer."}
    if text in ("open downloads", "open my downloads", "downloads", "downloads folder", "open downloads folder"):
        return {"tool": "open_folder", "params": {"name": "downloads"}, "speak": "Opening Downloads."}
    if text in ("open desktop", "open my desktop", "desktop", "desktop folder", "open desktop folder"):
        return {"tool": "open_folder", "params": {"name": "desktop"}, "speak": "Opening Desktop."}
    if text in ("open documents", "open my documents", "documents", "documents folder", "open documents folder"):
        return {"tool": "open_folder", "params": {"name": "documents"}, "speak": "Opening Documents."}
    if text in ("open pictures", "open my pictures", "pictures", "pictures folder", "open pictures folder", "photos"):
        return {"tool": "open_folder", "params": {"name": "pictures"}, "speak": "Opening Pictures."}
    if text in ("open music", "open my music", "music folder", "open music folder"):
        return {"tool": "open_folder", "params": {"name": "music"}, "speak": "Opening Music."}
    if text in ("open videos", "open my videos", "videos folder", "open videos folder"):
        return {"tool": "open_folder", "params": {"name": "videos"}, "speak": "Opening Videos."}

    # 8. Follow-up File / Choice References (e.g. "open number 1", "open that", "open the second one")
    if _RE_OPEN_FOLLOWUP.match(text):
        return {"tool": "open_file", "params": {"name": text}, "speak": ""}

    # No exact shortcut matched -> Allow fallthrough to Tier 2 LLM tool calling
    return None





# ---------------------------------------------------------------------------
# Tier 2: LLM Tool Calling & Structured Function Schema
# ---------------------------------------------------------------------------

AGENT_TOOL_DEFINITIONS = [
    {
        "name": "chat",
        "description": "General conversation, banter, games, humor, brainstorming, open-ended talk (e.g. 'let\\'s do something fun', 'tell me a joke', 'I\\'m bored', 'what can we do?'), entity lookups ('who is...', 'what is...'), technical/conceptual questions, definitions, explanations, and advice.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "web_search",
        "description": "Search Google for live facts, current news, stock prices, sports scores, or real-time online information.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Web search query"}}, "required": ["query"]},
    },
    {
        "name": "ask_document",
        "description": "Answer user questions, extract information, or retrieve facts from local documents, PDFs, reports, notes, or files.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "The question to answer based on document contents"}}, "required": ["question"]},
    },
    {
        "name": "find_document",
        "description": "Discover and list file paths on disk when the user explicitly asks to locate, browse, or list files.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search concept or keywords"}}, "required": ["query"]},
    },
    {
        "name": "open_file",
        "description": "Open a local document, report, PDF, spreadsheet, or follow-up selection in Windows.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Filename or document title to open"}}, "required": ["name"]},
    },
    {
        "name": "summarize_document",
        "description": "Summarize the contents of a local document or report.",
        "parameters": {"type": "object", "properties": {"filepath": {"type": "string", "description": "File path or name to summarize"}}},
    },
    {
        "name": "open_app",
        "description": "Launch installed Windows desktop software ONLY when the user explicitly requests to open, launch, or start a specific named application (e.g. 'open Spotify', 'launch Chrome', 'open Notepad'). Never use for vague or conversational requests.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Name of the application"}}, "required": ["name"]},
    },
    {
        "name": "close_app",
        "description": "Close a running desktop application or active window.",
        "parameters": {"type": "object", "properties": {"app_name": {"type": "string", "description": "Name of the application or 'window'"}}, "required": ["app_name"]},
    },
    {
        "name": "play_youtube",
        "description": "Play a song, artist, album, or video on YouTube.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Song title, artist, or video search term"}}, "required": ["query"]},
    },
    {
        "name": "open_website",
        "description": "Open a specific URL or web domain in the default browser.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "Full URL or domain to open"}}, "required": ["url"]},
    },
    {
        "name": "get_weather",
        "description": "Get current weather conditions and forecast for a city or local area.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "City name, or empty for local area"}}},
    },
    {
        "name": "open_folder",
        "description": "Open a system directory in Windows Explorer (downloads, desktop, documents, pictures, music, etc.).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Folder name"}}, "required": ["name"]},
    },
    {
        "name": "read_emails",
        "description": "Read and summarize recent emails from Outlook.",
        "parameters": {"type": "object", "properties": {"count": {"type": "integer", "description": "Number of emails to read (default: 5)"}}},
    },
    {
        "name": "search_emails",
        "description": "Search Outlook emails by keyword, sender, or subject.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keyword to search in emails"}}, "required": ["query"]},
    },
    {
        "name": "draft_email",
        "description": "Draft a new email with recipient, subject, and body.",
        "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "prompt": {"type": "string"}}},
    },
    {
        "name": "get_calendar",
        "description": "Get calendar appointments and upcoming meetings from Outlook.",
        "parameters": {"type": "object", "properties": {"days": {"type": "integer", "description": "Days ahead (1=today, 7=this week)"}}},
    },
    {
        "name": "search_calendar",
        "description": "Search Outlook calendar events by keyword.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Meeting keyword or person"}}, "required": ["query"]},
    },
    {
        "name": "search_knowledge",
        "description": "Search indexed local documents, files, records, memory, and personal knowledge base for information or user-specific facts.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search question or keywords"}}, "required": ["query"]},
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer.",
        "parameters": {"type": "object", "properties": {"duration": {"type": "integer", "description": "Seconds"}}, "required": ["duration"]},
    },
    {
        "name": "set_reminder",
        "description": "Schedule a reminder for a future time.",
        "parameters": {"type": "object", "properties": {"time": {"type": "string"}, "message": {"type": "string"}}, "required": ["message"]},
    },
    {
        "name": "take_screenshot",
        "description": "Capture the desktop screen.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_screen",
        "description": "Analyze what is currently visible on the screen.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
    },
    {
        "name": "wikipedia",
        "description": "Look up an encyclopedia article on Wikipedia.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "calculate",
        "description": "Evaluate a mathematical calculation.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
    },
]


def _build_tier2_system_prompt() -> str:
    """Builds clean tool specification system prompt for Tier 2 LLM routing."""
    tools_doc = []
    for t in AGENT_TOOL_DEFINITIONS:
        props = list(t["parameters"].get("properties", {}).keys())
        params_sig = "{" + ", ".join(f'"{p}": "..."' for p in props) + "}"
        tools_doc.append(f"- {t['name']} {params_sig} : {t['description']}")

    now = datetime.datetime.now()
    date_ctx = now.strftime("%A, %B %d, %Y at %I:%M %p")

    return (
        "You are Amigo's intent router. Select the best tool for the user's request.\n"
        "Return ONLY a JSON array containing the action object:\n"
        '[{"tool": "tool_name", "params": {"param": "value"}, "speak": ""}]\n\n'
        f"Current Time: {date_ctx}\n\n"
        "Available Tools:\n" + "\n".join(tools_doc) + "\n\n"
        "Rules:\n"
        "- For conversation, banter, humor, playful remarks, brainstorming, or open-ended talk (e.g. 'let\\'s do something fun', 'tell me a joke', 'I\\'m bored', 'what should we do?'), ALWAYS use 'chat'.\n"
        "- For general questions, conceptual queries (e.g. 'what is RAG', 'how do timers work?'), definitions, explanations, or advice, ALWAYS use 'chat'.\n"
        "- ONLY use 'open_app' when the user explicitly asks to launch/open a named application (e.g. 'open Spotify', 'launch Chrome'). Never launch an app on vague requests.\n"
        "- For real-time online lookups (e.g. stock prices, latest news, live scores), use 'web_search'.\n"
        "- When the user asks to OPEN or VIEW a specific file, document, PDF, or report, use 'open_file' with the target name.\n"
        "- When the user asks to FIND or SEARCH documents by concept/topic (e.g. 'find documents related to accounting and GST'), use 'find_document'.\n"
        "- For extracting specific numbers, dates, or data points inside user documents, use 'ask_document'.\n"
        "- Output strictly valid JSON."
    )


def resolve_intent_via_llm(user_query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Tier 2: Robust LLM function calling fallback for natural language, multi-keyword extraction,
    and fuzzy intent resolution.
    """
    llm = init_local_llm()
    if not llm:
        return [{"tool": "chat", "params": {}, "speak": ""}]

    try:
        system_prompt = _build_tier2_system_prompt()

        if conversation_history:
            recent = conversation_history[-4:]
            turns = [f"User: {c.get('user', '')}\nAssistant: {c.get('assistant', '')}" for c in recent if isinstance(c, dict) and (c.get("user") or c.get("assistant"))]
            if turns:
                system_prompt += "\n\n[Recent Conversation Context]:\n" + "\n".join(turns)

        user_prompt = f"User Request: {user_query}\nJSON Output:"

        res = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=250,
            temperature=0.05,
            stop=STOP_TOKENS,
        )

        raw = res["choices"][0]["message"]["content"].strip()
        if raw:
            # 1. Parse JSON array
            if arr_match := re.search(r"\[\s*\{.*?\}\s*\]", raw, re.DOTALL):
                parsed = json.loads(arr_match.group())
                if isinstance(parsed, list) and parsed:
                    return [{
                        "tool": str(a.get("tool", "chat")).strip(),
                        "params": a.get("params", {}) if isinstance(a.get("params"), dict) else {},
                        "speak": sanitize_for_tts(str(a.get("speak", ""))),
                    } for a in parsed if isinstance(a, dict)]

            # 2. Parse single JSON object
            if obj_match := re.search(r"\{[^{}]*\"tool\"\s*:\s*\"[^\"]+\"[^{}]*\}", raw, re.DOTALL):
                parsed = json.loads(obj_match.group())
                if isinstance(parsed, dict) and "tool" in parsed:
                    return [{
                        "tool": str(parsed.get("tool", "chat")).strip(),
                        "params": parsed.get("params", {}) if isinstance(parsed.get("params"), dict) else {},
                        "speak": sanitize_for_tts(str(parsed.get("speak", ""))),
                    }]

    except Exception as e:
        logger.debug("[Tier 2 LLM Routing Fallback Note]: %s", e)

    return [{"tool": "chat", "params": {}, "speak": ""}]


# ---------------------------------------------------------------------------
# Unified Hybrid Intent Entry Point (Tier 1 -> Tier 2)
# ---------------------------------------------------------------------------

def get_agent_action(user_query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Unified entry point combining Tier 1 (fast-path regex) and Tier 2 (LLM tool-calling fallback).
    """
    if not user_query or not user_query.strip():
        return [{"tool": "chat", "params": {}, "speak": ""}]

    # Security & Guardrail Check
    if _RE_PROBE_GUARD.search(user_query):
        return [{"tool": "chat", "params": {}, "speak": ""}]

    # Tier 1: Fast-Path Regex (Instant <0.1ms for unambiguous shortcuts)
    fast_action = parse_user_intent_fast(user_query)
    if fast_action is not None:
        return [fast_action]

    # Tier 2: LLM Tool-Calling Fallback (Handles fuzzy queries, complex arguments, semantic routing)
    return resolve_intent_via_llm(user_query, conversation_history)

