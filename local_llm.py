"""
Local LLM Module for Amigo Voice Assistant.
Executes MiniCPM 5 2B locally via llama-cpp-python on Windows.
"""

import base64
import contextvars
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
# Model Configuration (MiniCPM 5 2B)
# ---------------------------------------------------------------------------
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "models"))

MODEL_NAME = "MiniCPM 5 2B"
MODEL_FILENAME = "MiniCPM5-2B-Q4_K_M.gguf"
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)
MODEL_TARGETS = [
    ("openbmb/MiniCPM5-2B-GGUF", "MiniCPM5-2B-Q4_K_M.gguf"),
    ("bartowski/MiniCPM5-2B-GGUF", "MiniCPM5-2B-Q4_K_M.gguf"),
    ("Abiray/MiniCPM5-2B-GGUF", "MiniCPM5-2B-Q4_K_M.gguf"),
]

STOP_TOKENS = ["<|im_end|>", "<|endoftext|>", "<|im_start|>", "User:", "Human:", "Assistant:"]

AVAILABLE_MODELS = {
    "minicpm5-2b": {
        "key": "minicpm5-2b",
        "name": MODEL_NAME,
        "filename": MODEL_FILENAME,
        "path": MODEL_PATH,
        "targets": MODEL_TARGETS,
        "downloaded": os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 500_000_000,
        "size_gb": 1.56,
        "has_mmproj": False,
    },
}

_active_model_key = "minicpm5-2b"

_local_llm_instance = None
_llm_lock = threading.Lock()
_devnull = open(os.devnull, "w")


def get_available_models() -> dict:
    """Return dictionary of available models and their download status."""
    for m in AVAILABLE_MODELS.values():
        m["downloaded"] = os.path.exists(m["path"]) and os.path.getsize(m["path"]) > 500_000_000
    return AVAILABLE_MODELS


def set_active_model(model_key: str = "minicpm5-2b") -> bool:
    """Sets the active model key and reloads instance if needed."""
    global _active_model_key, _local_llm_instance
    if model_key in AVAILABLE_MODELS:
        _active_model_key = model_key
    with _llm_lock:
        _local_llm_instance = None
    init_local_llm(force_reload=True)
    return True


def get_active_model_info() -> dict:
    """Return metadata for the currently active model."""
    m_info = AVAILABLE_MODELS.get(_active_model_key, AVAILABLE_MODELS["minicpm5-2b"])
    m_path = m_info["path"]
    return {
        "key": _active_model_key,
        "name": m_info["name"],
        "filename": m_info["filename"],
        "path": m_path,
        "downloaded": os.path.exists(m_path) and os.path.getsize(m_path) > 500_000_000,
        "size_gb": m_info.get("size_gb", 1.56),
        "has_mmproj": m_info.get("has_mmproj", False),
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
    active_info = AVAILABLE_MODELS.get(_active_model_key, AVAILABLE_MODELS["minicpm5-2b"])
    target_path = active_info["path"]
    target_name = active_info["name"]
    target_list = active_info.get("targets", MODEL_TARGETS)
    return _download_hf_file(target_list, target_path, 500_000_000, target_name)


def is_vision_ready() -> bool:
    """Checks if native multimodal vision is available (MiniCPM 5 2B uses Windows Media OCR)."""
    return False



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
                _setup_chat_formatter(_local_llm_instance)
                logger.info(f"[Local AI Engine] {MODEL_NAME} loaded.")
                return _local_llm_instance
        except Exception as e:
            logger.error(f"[Local AI Engine] Load error: {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# Deep Thinking / Reasoning Mode Control
# ---------------------------------------------------------------------------
_thinking_ctx: contextvars.ContextVar[bool | None] = contextvars.ContextVar("thinking_ctx", default=None)
_thinking_enabled: bool = False


def set_thinking_enabled(enabled: bool) -> None:
    """Enable or disable deep thinking / step-by-step reasoning mode."""
    global _thinking_enabled
    _thinking_enabled = bool(enabled)
    logger.info("[AI Config] Deep Thinking Mode: %s", "ENABLED" if _thinking_enabled else "DISABLED")


def is_thinking_enabled() -> bool:
    """Return whether deep thinking mode is currently active."""
    return _thinking_enabled


def get_current_thinking_enabled() -> bool:
    """Return effective thinking state considering context overrides."""
    ctx_val = _thinking_ctx.get()
    if ctx_val is not None:
        return ctx_val
    return _thinking_enabled


def _setup_chat_formatter(llm) -> None:
    """Wraps Jinja2ChatFormatter to dynamically pass enable_thinking to the GGUF template."""
    try:
        from llama_cpp import llama_chat_format
        handler = llm.chat_handler or (llm._chat_handlers.get(llm.chat_format) if hasattr(llm, "_chat_handlers") else None)
        if not handler and hasattr(llm, "_chat_handlers"):
            handler = llm._chat_handlers.get("chat_template.default")
        if handler and hasattr(handler, "__closure__") and handler.__closure__:
            formatters = [c.cell_contents for c in handler.__closure__ if hasattr(c.cell_contents, "template")]
            if formatters:
                base_formatter = formatters[0]

                class ThinkingJinja2ChatFormatter:
                    def __init__(self, base):
                        self.base = base

                    def __call__(self, *args, **kwargs):
                        if "enable_thinking" not in kwargs:
                            kwargs["enable_thinking"] = get_current_thinking_enabled()
                        return self.base(*args, **kwargs)

                llm.chat_handler = llama_chat_format.chat_formatter_to_chat_completion_handler(
                    ThinkingJinja2ChatFormatter(base_formatter)
                )
    except Exception as e:
        logger.warning(f"[Chat Formatter Setup Warning]: {e}")


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
        prefix = text.split("<think>")[0].strip()
        if prefix:
            text = prefix
        else:
            # Token limit hit while thinking; extract the reasoning's final thoughts or answer draft
            thought_body = text.split("<think>", 1)[-1].strip()
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', thought_body) if len(s.strip()) > 5]
            text = sentences[-1] if sentences else thought_body
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
    thinking: bool | None = None,
    sanitize: bool = True,
) -> str:
    """Queries local LLM and returns clean spoken output."""
    llm = init_local_llm()
    if llm:
        token = None
        if thinking is not None:
            token = _thinking_ctx.set(thinking)
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
            if sanitize:
                return sanitize_for_tts(output.strip()) or output.strip()
            return output.strip()
        except Exception as e:
            logger.error(f"[LLM Query Error]: {e}")
        finally:
            if token is not None:
                _thinking_ctx.reset(token)
    return "I am here and ready to help."


def query_local_llm_stream(
    prompt: str | list[dict],
    system_prompt: str = "You are Amigo, a helpful voice assistant. Speak in clear, plain sentences.",
    max_tokens: int = 512,
    interruption_event: threading.Event | None = None,
    temperature: float = 0.6,
    thinking: bool | None = None,
) -> Generator[str, None, None]:
    """Streams raw tokens in real-time from the local LLM."""
    llm = init_local_llm()
    if llm:
        token = None
        if thinking is not None:
            token = _thinking_ctx.set(thinking)
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
                delta = chunk["choices"][0].get("delta", {}).get("content", "")
                if delta:
                    yield delta
            return
        except Exception as e:
            logger.error(f"[LLM Stream Error]: {e}")
        finally:
            if token is not None:
                _thinking_ctx.reset(token)
    yield "I am here and ready to help."


_RE_SENTENCE_SPLIT_CHUNKS = re.compile(r'(?<=[.!?])\s+|\n+')
_RE_TITLE_ABBREV = re.compile(r'\b(mr|mrs|ms|dr|vs|eg|ie|etc)\.$', re.IGNORECASE)


def stream_sentence_chunks(token_generator, interruption_event: threading.Event | None = None) -> Generator[str, None, None]:
    """Buffers token stream and yields complete sentence chunks immediately for TTS."""
    buffer = ""
    in_think = False

    for token in token_generator:
        if interruption_event and interruption_event.is_set():
            return
        buffer += token

        if "<think>" in buffer:
            in_think = True

        if in_think:
            if "</think>" in buffer:
                buffer = buffer.split("</think>", 1)[1]
                in_think = False
            else:
                continue

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

    if not in_think and buffer.strip() and not (interruption_event and interruption_event.is_set()):
        clean = sanitize_for_tts(buffer.strip())
        if clean:
            yield clean


# ---------------------------------------------------------------------------
# Multimodal Vision Stub (MiniCPM 5 2B uses native Windows Media OCR in screen_vision.py)
# ---------------------------------------------------------------------------

def query_local_vision(*args, **kwargs) -> str | None:
    """MiniCPM 5 2B is a text model; screen vision is powered by native Windows OCR."""
    return None



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tier 0: Emergency Stop & Fast Math Command Matcher
# ---------------------------------------------------------------------------

_RE_PROBE_GUARD = re.compile(
    r"\b(what (tools?|functions?|capabilities) do you have|list your tools|how were you built|"
    r"what model are you|what is your prompt|output your json|tell me a secret)\b",
    re.I
)

_EXACT_EXIT = frozenset({"exit", "quit", "goodbye", "bye", "close amigo", "shutdown pc"})
_EXACT_STOP = frozenset({"stop", "shut up", "be quiet", "stop talking", "stop speaking", "quiet", "silence"})
_RE_CALC_COMMAND = re.compile(r"^(?:calculate|compute|solve|evaluate)\s+(.+)$", re.IGNORECASE)

_RE_SPOKEN_PREFIX = re.compile(
    r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?(?:could\s+(?:you|u)\s+|can\s+(?:you|u)\s+|would\s+(?:you|u)\s+)?(?:please\s+)?(?:i\s+want\s+to\s+|tell\s+me\s+)?",
    re.IGNORECASE,
)
_RE_SPOKEN_SUFFIX = re.compile(r"\s+(?:please|for\s+me|boss|amigo)$", re.IGNORECASE)


def clean_spoken_query(query: str) -> str:
    """Normalizes spoken query by stripping conversational filler prefixes."""
    q = (query or "").lower().strip()
    q = _RE_SPOKEN_PREFIX.sub("", q).strip()
    q = _RE_SPOKEN_SUFFIX.sub("", q).strip()
    return q.rstrip(".!?,;:")


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    """
    Tier 0: Sub-millisecond matcher strictly for immediate safety exits, stops, and explicit math.
    All device actions and tool decisions are routed through Laya System 1.
    """
    text = clean_spoken_query(query)
    if not text:
        return {"tool": "chat", "params": {}, "speak": ""}

    if text in _EXACT_EXIT:
        return {"tool": "exit", "params": {}, "speak": "Goodbye!"}
    if text in _EXACT_STOP:
        return {"tool": "stop", "params": {}, "speak": "Stopped."}

    if m := _RE_CALC_COMMAND.match(text):
        expr = m.group(1).strip()
        if expr:
            return {"tool": "calculate", "params": {"expression": expr}, "speak": ""}

    return None


# ---------------------------------------------------------------------------
# Action & Intent Resolution via Laya System 1 & MiniCPM 5 2B
# ---------------------------------------------------------------------------

def get_agent_action(user_query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Primary intent entry point for Amigo Voice Assistant.
    Coordinates between emergency stops, Task Agent (desktop & browser navigation),
    Laya System 1 neural decision router (all actions), and MiniCPM 5 2B (conversation).
    """
    if not user_query or not user_query.strip():
        return [{"tool": "chat", "params": {}, "speak": ""}]

    # Security & Guardrail Check
    if _RE_PROBE_GUARD.search(user_query):
        return [{"tool": "chat", "params": {}, "speak": ""}]

    # Tier 0: Emergency Safety Stop / Exit
    fast_stop = parse_user_intent_fast(user_query)
    if fast_stop is not None:
        return [fast_stop]

    q_lower = user_query.lower().strip()

    # Tier 1: Multi-Step Compound Action Chains (Decompose commands like "open notepad and type Hello World")
    is_conversational_start = any(q_lower.startswith(p) for p in (
        "search ", "google ", "play ", "stream ", "watch ", "ask ", "tell me ",
        "what ", "who ", "why ", "how ", "is ", "are ", "can you explain",
        "calculate ", "compute ", "solve "
    ))
    if not is_conversational_start and any(conj in q_lower for conj in (" and then ", " then ", " after that ", " and ", " & ", ";")):
        try:
            import task_agent
            steps = task_agent.decompose_task(user_query)
            if len(steps) > 1:
                context = {}
                compound_actions = []
                has_executable_tool = False
                for sq in steps:
                    act = task_agent.resolve_step_intent(sq, context)
                    if act and act.get("tool") not in ("chat", None):
                        has_executable_tool = True
                        compound_actions.append(act)
                        if act.get("tool") == "open_app":
                            context["last_opened_app"] = act.get("params", {}).get("name", "")
                    else:
                        compound_actions.append({"tool": "chat", "params": {}, "speak": ""})
                if has_executable_tool and compound_actions:
                    return compound_actions
        except Exception as e:
            logger.debug("[Task Agent Decomposition Note]: %s", e)

    # Tier 2: Laya System 1 Neural Decision Router (Every action goes through Laya!)
    try:
        from laya_router import is_laya_ready, route_intent_via_laya
        if is_laya_ready():
            laya_action = route_intent_via_laya(user_query)
            if laya_action and laya_action.get("tool") not in ("chat", None):
                return [laya_action]
    except Exception as e:
        logger.debug("[Laya Router Exception]: %s", e)

    # Tier 3: Conversational Chat & Reasoning via MiniCPM 5 2B (Amigo Neural Model)
    return [{"tool": "chat", "params": {}, "speak": ""}]




