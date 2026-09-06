"""
Local LLM Module for Amigo Voice Assistant.
Executes Qwen 3.5 2B Instruct locally via llama-cpp-python on Windows.
"""

import base64
import datetime
import io
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

from network_utils import is_internet_connected


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

# Multimodal Projector (Vision) Configuration
MMPROJ_FILENAME = "qwen3.5-2b-mmproj.gguf"
MMPROJ_PATH = os.path.join(MODEL_DIR, MMPROJ_FILENAME)
MMPROJ_TARGETS = [
    ("bartowski/Qwen_Qwen3.5-2B-GGUF", "mmproj-Qwen_Qwen3.5-2B-bf16.gguf"),
    ("unsloth/Qwen3.5-2B-GGUF", "mmproj-BF16.gguf"),
    ("bartowski/Qwen_Qwen3.5-2B-GGUF", "mmproj-Qwen_Qwen3.5-2B-f16.gguf"),
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
        "vision_ready": is_vision_ready(),
    }


def _download_hf_file(targets: list[tuple[str, str]], target_path: str, min_size: int, label: str) -> str:
    """Robust model and projector downloader with chunked streaming fallback."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path) and os.path.getsize(target_path) >= min_size:
        return target_path

    logger.info(f"Downloading {label}...")
    for repo, fname in targets:
        # Method 1: huggingface_hub
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(repo_id=repo, filename=fname, local_dir=MODEL_DIR)
            if os.path.exists(downloaded) and os.path.getsize(downloaded) >= min_size:
                if downloaded != target_path and not os.path.exists(target_path):
                    try:
                        os.replace(downloaded, target_path)
                        downloaded = target_path
                    except Exception:
                        pass
                logger.info(f"Downloaded {label} to {downloaded}")
                return downloaded
        except Exception as e:
            logger.debug(f"huggingface_hub download for {repo}/{fname} failed: {e}")

        # Method 2: Direct chunked streaming (bypasses HTTP/2 handshake resets)
        try:
            import requests
            url = f"https://huggingface.co/{repo}/resolve/main/{fname}"
            logger.info(f"Streaming {label} from {url}...")
            tmp_path = target_path + ".tmp"
            with requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=2 * 1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) >= min_size:
                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                    except Exception:
                        pass
                os.replace(tmp_path, target_path)
                logger.info(f"Successfully downloaded {label} via direct stream.")
                return target_path
        except Exception as e:
            logger.warning(f"Direct stream download for {repo}/{fname} failed: {e}")
            time.sleep(1)

    return target_path


def get_model_path() -> str:
    """Ensure active model weights exist locally; download if needed."""
    active_info = AVAILABLE_MODELS.get(_active_model_key, AVAILABLE_MODELS["qwen-3.5-2b"])
    target_path = active_info["path"]
    target_name = active_info["name"]
    target_list = active_info.get("targets", MODEL_TARGETS)
    return _download_hf_file(target_list, target_path, 500_000_000, target_name)


def get_mmproj_path() -> str | None:
    """Returns local path to multimodal vision projector, or None if not yet downloaded."""
    if os.path.exists(MMPROJ_PATH) and os.path.getsize(MMPROJ_PATH) > 50_000_000:
        return MMPROJ_PATH
    for _, fname in MMPROJ_TARGETS:
        alt_path = os.path.join(MODEL_DIR, fname)
        if os.path.exists(alt_path) and os.path.getsize(alt_path) > 50_000_000:
            return alt_path
    return None


def ensure_mmproj_downloaded() -> str | None:
    """Explicit helper to trigger and verify download of Qwen 3.5 2B vision projector."""
    res = _download_hf_file(MMPROJ_TARGETS, MMPROJ_PATH, 50_000_000, "Qwen 3.5 2B Vision Projector")
    if res and os.path.exists(res) and os.path.getsize(res) > 50_000_000:
        return res
    return None


def is_vision_ready() -> bool:
    """Checks if native multimodal vision is available."""
    return get_mmproj_path() is not None


def ensure_model_downloaded() -> str:
    """Explicit helper to trigger and verify model download."""
    return get_model_path()


def init_local_llm(force_reload: bool = False):
    """Initializes local Llama instance with GPU offloading, fast context ingestion, and multimodal vision support."""
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
            for fa in [True, False]:
                try:
                    kwargs = {
                        "model_path": model_file,
                        "n_ctx": 4096,
                        "n_gpu_layers": -1,
                        "main_gpu": 0,
                        "n_threads": max(1, multiprocessing.cpu_count() - 2),
                        "n_batch": 1024,
                        "n_ubatch": 512,
                        "flash_attn": fa,
                        "use_mmap": True,
                        "verbose": False,
                    }
                    _local_llm_instance = Llama(**kwargs)
                    if _local_llm_instance:
                        break
                except Exception:
                    pass

            if _local_llm_instance:
                logger.info(f"[Local AI Engine] {MODEL_NAME} loaded.")
                return _local_llm_instance
        except Exception as e:
            logger.error(f"[Local AI Engine] Load error: {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# TTS Text Cleaning Utilities (Optimized with pre-compiled regexes)
# ---------------------------------------------------------------------------

_RE_THINK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_RE_CODE_BLOCK = re.compile(r"```[a-zA-Z]*\n?([\s\S]*?)```")
_RE_MD_MARKS = re.compile(r"[*_~`#>]")
_RE_MD_LINKS = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_BRACKETS = re.compile(r"[{}\[\]\\<>]")
_RE_WHITESPACE = re.compile(r"\s+")


def strip_markdown_for_tts(text: str) -> str:
    """Strips markdown, thought tags, and formatting characters so TTS sounds natural."""
    if not text:
        return ""
    # Strip any thought blocks: text inside <think>...</think> is internal reasoning
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    elif "<think>" in text:
        # If <think> was never closed (token limit hit during thinking),
        # keep only what came before <think>, never leak internal thoughts
        text = text.split("<think>")[0].strip()
    text = text.replace("₹", " rupees ").replace("$", " dollars ").replace("€", " euros ").replace("£", " pounds ")
    text = _RE_CODE_BLOCK.sub(r"\1", text)
    text = _RE_MD_MARKS.sub("", text)
    text = _RE_MD_LINKS.sub(r"\1", text)
    text = _RE_BRACKETS.sub(" ", text)
    return _RE_WHITESPACE.sub(" ", text).strip()


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
    temperature: float = 0.6,
) -> str:
    """Queries local LLM and returns clean spoken output."""
    llm = init_local_llm()
    if llm:
        try:
            messages = _prepare_chat_messages(system_prompt, prompt)
            res = llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                repeat_penalty=1.1,
                stop=STOP_TOKENS,
            )
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
    temperature: float = 0.6,
) -> Generator[str, None, None]:
    """Streams raw tokens in real-time from the local LLM."""
    llm = init_local_llm()
    if llm:
        try:
            messages = _prepare_chat_messages(system_prompt, prompt)
            stream_res = llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                repeat_penalty=1.1,
                stop=STOP_TOKENS,
                stream=True,
            )
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


_RE_SENTENCE_SPLIT_CHUNKS = re.compile(r'(?<=[.!?])\s+|\n+')
_RE_TITLE_ABBREV = re.compile(r'\b(mr|mrs|ms|dr|vs|eg|ie|etc)\.$', re.IGNORECASE)


def stream_sentence_chunks(token_generator, interruption_event: threading.Event | None = None) -> Generator[str, None, None]:
    """Buffers token stream and yields complete sentence chunks immediately for TTS."""
    buffer = ""

    for token in token_generator:
        if interruption_event and interruption_event.is_set():
            return
        buffer += token
        for tok in STOP_TOKENS:
            if tok in buffer:
                buffer = buffer.split(tok)[0]

        splits = _RE_SENTENCE_SPLIT_CHUNKS.split(buffer)
        if len(splits) > 1:
            for s in splits[:-1]:
                cand = s.strip()
                if cand and not _RE_TITLE_ABBREV.search(cand):
                    clean = sanitize_for_tts(cand)
                    if clean:
                        yield clean
            buffer = splits[-1]

    if buffer.strip() and not (interruption_event and interruption_event.is_set()):
        clean = sanitize_for_tts(buffer.strip())
        if clean:
            yield clean


# ---------------------------------------------------------------------------
# Multimodal Native Vision Inference (Qwen 3.5 2B Native Vision)
# ---------------------------------------------------------------------------

def _pil_to_base64_url(img) -> str:
    """Converts PIL image to JPEG base64 data URL, downsampling to 768px max dimension for fast multimodal inference."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    max_dim = 768
    if max(img.size) > max_dim:
        img = img.copy()
        img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _image_to_base64_url(image_input) -> str | None:
    """Encodes PIL Image, file path, or raw bytes into a data URL for multimodal LLM ingestion."""
    try:
        from PIL import Image
        if isinstance(image_input, str):
            if not os.path.exists(image_input):
                return None
            with Image.open(image_input) as img:
                return _pil_to_base64_url(img)
        elif isinstance(image_input, bytes):
            with Image.open(io.BytesIO(image_input)) as img:
                return _pil_to_base64_url(img)
        elif hasattr(image_input, "save"):  # PIL Image instance
            return _pil_to_base64_url(image_input)
    except Exception as e:
        logger.error(f"[Vision] Image encoding error: {e}")
    return None


def query_local_vision(
    image_input,
    prompt: str = "Describe what you see on the screen in clear detail.",
    system_prompt: str = "You are Amigo, a helpful voice assistant with screen vision. Speak in natural plain English without markdown or bullet points.",
    max_tokens: int = 350,
    temperature: float = 0.5,
) -> str | None:
    """
    Executes native vision inference directly on an image/screenshot using Qwen 3.5 2B.
    Returns spoken plain text, or None if vision is not ready or encounters an error.
    """
    llm = init_local_llm()
    if not llm:
        return None

    v_handler = get_vision_chat_handler()
    if not v_handler:
        return None

    data_url = _image_to_base64_url(image_input)
    if not data_url:
        return None

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    llm.chat_handler = v_handler
    try:
        res = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            repeat_penalty=1.1,
            stop=STOP_TOKENS,
        )
        output = res["choices"][0]["message"]["content"]
        for tok in STOP_TOKENS:
            output = output.split(tok)[0]
        return sanitize_for_tts(output.strip()) or output.strip()
    except Exception as e:
        logger.error(f"[Vision Query Error]: {e}")
        return None
    finally:
        llm.chat_handler = None


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
_RE_CONTEXTUAL_REFERENCE = re.compile(
    r"^(?:that|this|it|them|those|these|the\s+same)(?:\s+(?:one|song|video|track|file|doc|document|app|tab|site|website|link|page|media|audio|playback))?$",
    re.IGNORECASE,
)
_RE_OPEN_CHOICE = re.compile(
    r"^(?:open|show|display|view|launch)\s+(?:the\s+first\s+one|first\s+one|the\s+second\s+one|the\s+third\s+one|first|second|third|fourth|fifth|number\s+\d+|\d+)$",
    re.IGNORECASE,
)

_RE_SPOKEN_PREFIX = re.compile(
    r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?(?:could\s+(?:you|u)\s+|can\s+(?:you|u)\s+|would\s+(?:you|u)\s+)?(?:please\s+)?(?:i\s+want\s+to\s+|tell\s+me\s+)?",
    re.IGNORECASE,
)
_RE_SPOKEN_SUFFIX = re.compile(r"\s+(?:please|for\s+me|boss|amigo)$", re.IGNORECASE)
_RE_PLAY_YOUTUBE = re.compile(r"^(?:play|stream|listen to|watch|put on)\s+(?:a\s+|the\s+|some\s+)?(.+)$", re.IGNORECASE)

_EXACT_EXIT = frozenset({"exit", "quit", "goodbye", "bye", "close amigo", "shutdown pc"})
_EXACT_STOP = frozenset({"stop", "shut up", "be quiet", "stop talking", "stop speaking", "quiet", "silence"})
_EXACT_LOCK = frozenset({"lock pc", "lock my pc", "lock computer", "lock screen", "lock workstation"})
_EXACT_SLEEP = frozenset({"sleep pc", "sleep computer", "put pc to sleep", "sleep"})
_EXACT_CANCEL_SHUTDOWN = frozenset({"cancel shutdown", "cancel restart"})
_EXACT_EMPTY_BIN = frozenset({"empty recycle bin", "empty trash", "clear recycle bin", "clear trash"})
_EXACT_CANCEL_REMINDER = frozenset({"cancel reminder", "cancel reminders", "cancel my reminders", "cancel timers"})
_EXACT_VOL_UP = frozenset({"volume up", "increase volume", "louder"})
_EXACT_VOL_DOWN = frozenset({"volume down", "decrease volume", "quieter"})
_EXACT_MUTE = frozenset({"mute", "unmute", "silence", "mute audio", "unmute audio"})
_EXACT_NEXT_TRACK = frozenset({"next track", "next song", "skip song", "skip track", "next"})
_EXACT_PREV_TRACK = frozenset({"previous track", "prev track", "prev song", "previous song", "previous"})
_EXACT_PAUSE_MEDIA = frozenset({"pause", "pause music", "pause playback", "stop music", "stop playback"})
_EXACT_RESUME_MEDIA = frozenset({"resume", "unpause", "continue music", "play music", "resume playback"})
_EXACT_SYS_STATUS = frozenset({"system status", "cpu usage", "ram usage", "memory usage", "battery status", "hardware metrics"})
_EXACT_CALENDAR = frozenset({"today's calendar", "today's schedule", "my calendar", "my schedule", "what's on my calendar today"})
_EXACT_EMAILS = frozenset({"unread emails", "check unread emails", "any unread emails", "new emails", "check new emails"})
_EXACT_STOPWATCH = frozenset({"stopwatch", "start stopwatch", "open stopwatch"})
_EXACT_EXPLORER = frozenset({"windows explorer", "file explorer", "explorer", "open windows explorer", "open file explorer", "open explorer", "open this pc", "this pc", "my computer"})
_EXACT_DOWNLOADS = frozenset({"open downloads", "open my downloads", "downloads", "downloads folder", "open downloads folder"})
_EXACT_DESKTOP = frozenset({"open desktop", "open my desktop", "desktop", "desktop folder", "open desktop folder"})
_EXACT_DOCUMENTS = frozenset({"open documents", "open my documents", "documents", "documents folder", "open documents folder"})
_EXACT_PICTURES = frozenset({"open pictures", "open my pictures", "pictures", "pictures folder", "open pictures folder", "photos"})
_EXACT_MUSIC = frozenset({"open music", "open my music", "music folder", "open music folder"})
_EXACT_VIDEOS = frozenset({"open videos", "open my videos", "videos folder", "open videos folder"})
_EXACT_CURRENT_MEDIA = frozenset({
    "what is playing", "what's playing", "whats playing",
    "what song is this", "what song is playing", "what are you playing",
    "current song", "current track", "what is this song", "what video is this",
    "what is currently playing", "what media is playing", "now playing", "playing now",
})
_EXACT_TIME = frozenset({"what time is it", "what's the time", "whats the time", "current time", "the time", "tell me the time", "time"})
_EXACT_DATE = frozenset({"what is today's date", "what's today's date", "whats the date", "what date is it", "current date", "today's date", "todays date", "date"})


def clean_spoken_query(query: str) -> str:
    """Normalizes spoken query by stripping conversational filler prefixes."""
    q = (query or "").lower().strip()
    q = _RE_SPOKEN_PREFIX.sub("", q).strip()
    q = _RE_SPOKEN_SUFFIX.sub("", q).strip()
    return q.rstrip(".!?,;:")


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    """
    Tier 1: Ultra-fast (<0.05ms) matcher for exact, unambiguous shortcuts.
    Returns None immediately if the command requires natural language understanding.
    """
    text = clean_spoken_query(query)
    if not text:
        return {"tool": "chat", "params": {}, "speak": ""}

    # 1. Exact System & Power Actions
    if text in _EXACT_EXIT:
        return {"tool": "exit", "params": {}, "speak": "Goodbye!"}
    if text in _EXACT_STOP:
        return {"tool": "stop", "params": {}, "speak": "Stopped."}
    if text in _EXACT_LOCK:
        return {"tool": "lock_pc", "params": {}, "speak": "Locking your PC."}
    if text in _EXACT_SLEEP:
        return {"tool": "sleep_pc", "params": {}, "speak": "Putting system to sleep."}
    if text in _EXACT_CANCEL_SHUTDOWN:
        return {"tool": "cancel_shutdown", "params": {}, "speak": "Shutdown cancelled."}
    if text in _EXACT_EMPTY_BIN:
        return {"tool": "empty_recycle_bin", "params": {}, "speak": "Recycle bin emptied."}
    if text in _EXACT_CANCEL_REMINDER:
        return {"tool": "cancel_reminder", "params": {}, "speak": ""}

    # 2. Exact Volume / Brightness levels (prioritize explicit percentage)
    if m := _RE_VOL_LEVEL.search(text):
        return {"tool": "set_volume", "params": {"level": m.group(1)}, "speak": f"Setting volume to {m.group(1)} percent."}
    if m := _RE_BRIGHT_LEVEL.match(text):
        return {"tool": "set_brightness", "params": {"level": m.group(1)}, "speak": f"Setting brightness to {m.group(1)} percent."}

    # 3. Exact Media & Audio Shortcuts
    if text in _EXACT_VOL_UP:
        return {"tool": "volume_up", "params": {}, "speak": "Volume increased."}
    if text in _EXACT_VOL_DOWN:
        return {"tool": "volume_down", "params": {}, "speak": "Volume decreased."}
    if text in _EXACT_MUTE:
        return {"tool": "mute", "params": {}, "speak": "Audio toggled."}
    if text in _EXACT_NEXT_TRACK:
        return {"tool": "next_track", "params": {}, "speak": "Next track."}
    if text in _EXACT_PREV_TRACK:
        return {"tool": "prev_track", "params": {}, "speak": "Previous track."}
    if text in _EXACT_PAUSE_MEDIA:
        return {"tool": "pause_media", "params": {}, "speak": "Media paused."}
    if text in _EXACT_RESUME_MEDIA:
        return {"tool": "play_media", "params": {}, "speak": "Media resumed."}
    if text in _EXACT_CURRENT_MEDIA:
        return {"tool": "get_current_media", "params": {}, "speak": ""}
    if text in _EXACT_TIME:
        return {"tool": "get_time", "params": {}, "speak": ""}
    if text in _EXACT_DATE:
        return {"tool": "get_date", "params": {}, "speak": ""}
    if m := _RE_PLAY_YOUTUBE.match(text):
        target = m.group(1).strip()
        if target.lower() in ("youtube", "open youtube"):
            return {"tool": "open_website", "params": {"url": "https://www.youtube.com"}, "speak": "Opening YouTube."}
        if target and not _RE_CONTEXTUAL_REFERENCE.match(target) and target.lower() not in ("media", "playback", "audio", "again"):
            return {"tool": "play_youtube", "params": {"query": target}, "speak": f"Playing {target}."}

    # 4. Exact Hardware Metrics
    if text in _EXACT_SYS_STATUS:
        return {"tool": "system_status", "params": {}, "speak": "Checking system status."}

    # 5. Exact Calendar & Email Shortcuts
    if text in _EXACT_CALENDAR:
        return {"tool": "get_calendar", "params": {"days": 1}, "speak": "Checking today's schedule."}
    if text in _EXACT_EMAILS:
        return {"tool": "unread_emails", "params": {}, "speak": "Checking unread emails."}

    # 6. Exact Timer / Stopwatch Shortcuts
    if text in _EXACT_STOPWATCH:
        return {"tool": "stopwatch", "params": {"mode": "stopwatch"}, "speak": "Starting stopwatch."}
    if m := _RE_TIMER_SIMPLE.match(text):
        qty = int(m.group(1))
        unit = m.group(2).lower()
        secs = qty * 3600 if "h" in unit else qty * 60 if "m" in unit else qty
        return {"tool": "set_timer", "params": {"duration": secs, "seconds": secs}, "speak": f"Setting a {qty} {unit} timer."}

    # 7. Exact System Folder & Explorer Shortcuts
    if text in _EXACT_EXPLORER:
        return {"tool": "open_folder", "params": {"name": "explorer"}, "speak": "Opening File Explorer."}
    if text in _EXACT_DOWNLOADS:
        return {"tool": "open_folder", "params": {"name": "downloads"}, "speak": "Opening Downloads."}
    if text in _EXACT_DESKTOP:
        return {"tool": "open_folder", "params": {"name": "desktop"}, "speak": "Opening Desktop."}
    if text in _EXACT_DOCUMENTS:
        return {"tool": "open_folder", "params": {"name": "documents"}, "speak": "Opening Documents."}
    if text in _EXACT_PICTURES:
        return {"tool": "open_folder", "params": {"name": "pictures"}, "speak": "Opening Pictures."}
    if text in _EXACT_MUSIC:
        return {"tool": "open_folder", "params": {"name": "music"}, "speak": "Opening Music."}
    if text in _EXACT_VIDEOS:
        return {"tool": "open_folder", "params": {"name": "videos"}, "speak": "Opening Videos."}

    # 8. Follow-up File Choice Selection (e.g. "open number 1", "open the second one")
    if _RE_OPEN_CHOICE.match(text):
        return {"tool": "open_file", "params": {"name": text}, "speak": ""}

    # No exact shortcut matched -> Allow fallthrough to Tier 2 LLM tool calling
    return None






# ---------------------------------------------------------------------------
# Tier 2: LLM Tool Calling & Structured Function Schema
# ---------------------------------------------------------------------------

AGENT_TOOL_DEFINITIONS = [
    {
        "name": "chat",
        "description": "General conversation, banter, games, humor, brainstorming, open-ended talk (e.g. 'let\\'s do something fun', 'tell me a joke', 'I\\'m bored', 'what can we do?'), and questions about yourself or your capabilities.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "web_search",
        "description": "Search the web for entities, people, organizations, facts, current news, stock prices, definitions, or real-time information.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Web search query"}}, "required": ["query"]},
    },
    {
        "name": "ask_document",
        "description": "Answer user questions, extract numbers, PANs, PINs, totals, dates, or retrieve facts from local documents, invoices, PDFs, spreadsheets, reports, notes, or files.",
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
        "name": "get_current_media",
        "description": "Check what song, video, or media is currently playing.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_time",
        "description": "Get the current time.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_date",
        "description": "Get today's current date.",
        "parameters": {"type": "object", "properties": {}},
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
        "name": "calculate",
        "description": "Evaluate a mathematical calculation.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
    },
]


# Pre-render static tool specifications and rules once at module load
_PRECOMPUTED_TOOLS_DOC = "\n".join(
    f"- {t['name']} " + "{" + ", ".join(f'"{p}": "..."' for p in t["parameters"].get("properties", {}).keys()) + "} : " + t["description"]
    for t in AGENT_TOOL_DEFINITIONS
)

_PRECOMPUTED_RULES_DOC = (
    "Rules:\n"
    "- Context & Coreference Resolution: When the user refers to previously mentioned entities, topics, files, songs, videos, apps, or items using demonstratives or pronouns (e.g. 'that song', 'open that file', 'play it', 'search for that', 'what about it', 'open that app', 'tell me more about that'), ALWAYS resolve the exact target entity, title, or name from [Recent Conversation Context]. Never output literal placeholders like 'that song', 'that file', or 'it' in tool parameters.\n"
    "- When Internet Status is Connected (Online), ALWAYS route live real-time lookups, entity questions (e.g. 'tell me about X', 'who is X', 'what is X'), stock prices, latest news, live scores, current weather, and web lookups to 'web_search'. Disregard any past turns in conversation history that mentioned being offline.\n"
    "- When Internet Status is Disconnected (Offline) and the user requests an online action (like 'web_search' or looking up online info), route to 'chat' so you can explain to the user that you are not connected to the internet.\n"
    "- For conversation, banter, humor, playful remarks, brainstorming, or open-ended talk (e.g. 'let\\'s do something fun', 'tell me a joke', 'I\\'m bored', 'what should we do?'), ALWAYS use 'chat'.\n"
    "- For questions about your capabilities, abilities, or what you can do (e.g. 'can you web search?', 'can you search for me?', 'what can you do?'), ALWAYS use 'chat'.\n"
    "- For general conceptual queries and advice, use 'chat'. For any queries about real-world facts, people, companies, or things, use 'web_search'.\n"
    "- ONLY use 'open_app' when the user explicitly asks to launch/open a named application (e.g. 'open Spotify', 'launch Chrome'). Never launch an app on vague requests.\n"
    "- For real-time online lookups and specific search topics (e.g. stock prices, latest news, live scores, 'search for Python tutorials'), use 'web_search'. Never use 'web_search' without an actual subject to search.\n"
    "- For 'play_youtube', extract the target song, video, or artist in 'query'.\n"
    "- When the user asks to OPEN or VIEW a specific file, document, PDF, or report, use 'open_file' with the target name.\n"
    "- For extracting specific numbers, PAN, PIN, dates, amounts, or details inside user documents, invoices, or files (even if the user says 'find' or 'search' for a detail in a document), ALWAYS use 'ask_document', NEVER use 'find_document'. Use 'find_document' ONLY when the user asks to locate or browse files themselves on disk.\n"
    "- Output strictly valid JSON."
)

_RE_JSON_ARRAY = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)
_RE_JSON_OBJECT = re.compile(r"\{[^{}]*\"tool\"\s*:\s*\"[^\"]+\"[^{}]*\}", re.DOTALL)


def _build_tier2_system_prompt() -> str:
    """Builds clean tool specification system prompt for Tier 2 LLM routing with zero string rebuild overhead."""
    now = datetime.datetime.now()
    date_ctx = now.strftime("%A, %B %d, %Y at %I:%M %p")
    online = is_internet_connected()
    net_status = "Connected (Online)" if online else "Disconnected (Offline)"
    net_directive = (
        "Active Internet Connectivity: ONLINE.\n"
        "- The system is currently connected to the internet.\n"
        "- Any past messages in conversation history mentioning being offline or disconnected are obsolete because the device is now connected.\n"
        "- For any requests requiring real-time facts, stock quotes, market prices, latest news, or online info, route to 'web_search'."
        if online else
        "Active Internet Connectivity: OFFLINE.\n"
        "- No internet connection is currently available.\n"
        "- Route online queries (like web searches, YouTube streaming, or live data lookups) to 'chat' so you can inform the user."
    )

    return (
        "You are Amigo's intent router. Select the best tool for the user's request.\n"
        "Return ONLY a JSON array containing the action object:\n"
        '[{"tool": "tool_name", "params": {"param": "value"}, "speak": ""}]\n\n'
        "Do not output <think> tags, internal reasoning, or conversational commentary. Immediately return the JSON array.\n\n"
        f"Current Time: {date_ctx}\n"
        f"Internet Status: {net_status}\n"
        f"{net_directive}\n\n"
        f"Available Tools:\n{_PRECOMPUTED_TOOLS_DOC}\n\n"
        f"{_PRECOMPUTED_RULES_DOC}"
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
            max_tokens=350,
            temperature=0.05,
            stop=STOP_TOKENS,
        )

        raw = res["choices"][0]["message"]["content"].strip()
        if raw:
            # Strip markdown fences if wrapped in ```json ... ```
            clean_raw = re.sub(r"^```(?:json)?\s*", "", raw)
            clean_raw = re.sub(r"\s*```$", "", clean_raw).strip()

            parsed = None
            # Direct parse
            try:
                parsed = json.loads(clean_raw)
            except Exception:
                pass

            # Try finding [ ... ] with balanced/greedy brackets
            if parsed is None:
                start_bracket = clean_raw.find("[")
                end_bracket = clean_raw.rfind("]")
                if start_bracket != -1 and end_bracket > start_bracket:
                    try:
                        parsed = json.loads(clean_raw[start_bracket:end_bracket + 1])
                    except Exception:
                        pass

            # Try finding { ... }
            if parsed is None:
                start_brace = clean_raw.find("{")
                end_brace = clean_raw.rfind("}")
                if start_brace != -1 and end_brace > start_brace:
                    try:
                        parsed = json.loads(clean_raw[start_brace:end_brace + 1])
                    except Exception:
                        pass

            # Regex extraction fallback: extract "tool" and optional "question"/"query"/"filepath"/"name"
            if parsed is None:
                tool_match = re.search(r'"tool"\s*:\s*"([a-zA-Z0-9_]+)"', clean_raw)
                if tool_match:
                    tool_name = tool_match.group(1)
                    fallback_params = {}
                    for key in ("question", "query", "filepath", "name", "action"):
                        m_val = re.search(rf'"{key}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', clean_raw)
                        if m_val:
                            fallback_params[key] = m_val.group(1)
                    parsed = [{"tool": tool_name, "params": fallback_params}]

            if isinstance(parsed, dict):
                parsed = [parsed]

            if isinstance(parsed, list) and parsed:
                results = []
                for a in parsed:
                    if isinstance(a, dict):
                        t = str(a.get("tool", "chat")).strip()
                        spk = "" if t == "chat" else sanitize_for_tts(str(a.get("speak") or (a.get("params", {}).get("speak") if isinstance(a.get("params"), dict) else "") or ""))
                        results.append({
                            "tool": t,
                            "params": a.get("params", {}) if isinstance(a.get("params"), dict) else {},
                            "speak": spk,
                        })
                if results:
                    return results

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

