"""
Local LLM Module for Amigo Voice Assistant.
Executes Qwen 2.5 3B Instruct 100% locally via llama-cpp-python on Windows.
No C++ compiler or API keys required for local operation.

Public API
----------
sanitize_for_tts(text)          -> str   Combined markdown strip + symbol clean
strip_markdown_for_tts(text)    -> str   Markdown-only strip
clean_tts_text(text)            -> str   Symbol/emoji clean
query_local_llm(prompt, ...)    -> str   Raw LLM call (conversational chat)
query_local_llm_stream(prompt, ...) -> Generator[str] Real-time token generator
stream_sentence_chunks(tokens, ...)  -> Generator[str] Real-time sentence chunk generator
get_agent_action(query, ...)    -> dict  Agentic tool dispatch
init_local_llm()                -> Llama | None
get_active_model_info()         -> dict  Active model metadata
get_clipboard_text()            -> str | None
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
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("amigo.local_llm")

# ---------------------------------------------------------------------------
# Optional third-party context helpers
# ---------------------------------------------------------------------------
try:
    import pyperclip
except ImportError:
    pyperclip = None


def get_clipboard_text() -> str | None:
    """Return up to 1000 chars from the system clipboard, or None."""
    if pyperclip:
        try:
            text = pyperclip.paste()
            if text and isinstance(text, str):
                return text[:1000].strip()
        except Exception:
            pass
    return None

# ---------------------------------------------------------------------------
# Qwen 2.5 3B Model Configuration
# ---------------------------------------------------------------------------
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "models"))
MODEL_NAME = "Qwen 2.5 3B Instruct"
MODEL_FILENAME = "qwen2.5-3b-instruct-q4_k_m.gguf"
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)

MODEL_TARGETS = [
    ("Qwen/Qwen2.5-3B-Instruct-GGUF", "qwen2.5-3b-instruct-q4_k_m.gguf"),
    ("bartowski/Qwen2.5-3B-Instruct-GGUF", "Qwen2.5-3B-Instruct-Q4_K_M.gguf"),
    ("unsloth/Qwen2.5-3B-Instruct-GGUF", "Qwen2.5-3B-Instruct-Q4_K_M.gguf"),
]

STOP_TOKENS = ["<|im_end|>", "<|endoftext|>", "<|im_start|>", "User:", "Human:"]

AVAILABLE_MODELS = {
    "qwen-2.5-3b": {
        "key": "qwen-2.5-3b",
        "name": MODEL_NAME,
        "filename": MODEL_FILENAME,
        "path": MODEL_PATH,
        "targets": MODEL_TARGETS,
        "downloaded": os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 500_000_000,
        "size_gb": 2.05,
    }
}

_active_model_key = "qwen-2.5-3b"

def get_current_model_config() -> dict:
    """Returns the config dictionary for the active model."""
    return {
        "key": "qwen-2.5-3b",
        "name": MODEL_NAME,
        "filename": MODEL_FILENAME,
        "path": MODEL_PATH,
        "targets": MODEL_TARGETS,
        "downloaded": os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 500_000_000,
        "size_gb": 2.05,
    }

_local_llm_instance = None
_llm_lock = threading.Lock()

# Persistent devnull handle — avoids opening/closing a file on every LLM call
_devnull = open(os.devnull, "w")

# ---------------------------------------------------------------------------
# Agentic Tool-Calling System Prompt (Tuned for Qwen 2.5 3B Instruct)
# ---------------------------------------------------------------------------
AGENT_SYSTEM_PROMPT = """\
You are an agentic voice assistant running on a desktop computer. The user has spoken a command.
Analyze the user's intent and return your decision as a JSON array of action objects.
Output ONLY the JSON array — no markdown, no explanation, no preamble.

Each action object must follow this exact structure:
{"tool": "tool_name", "params": {}, "speak": "one short sentence for TTS or empty string"}

DECISION PRINCIPLES:
1. INTENT ROUTING:
   - "chat": For casual conversation, greetings, user questions, questions about yourself, reasoning, advice, general knowledge, or discussions.
   - "web_search": For questions seeking real-world facts, current events, public figures, news, scores, prices, or external online knowledge.
   - "hardware_metrics": For computer hardware performance queries (CPU load, RAM memory usage, battery percentage).
   - Device & OS action tools (open_app, play_youtube, set_timer, volume_up, etc.): When the user explicitly requests an action or system control.
2. CURRENT TURN ONLY:
   - Make decisions based strictly on the user's latest message. [Recent Conversation] is provided only to resolve pronouns and follow-up references; never repeat actions from previous turns.
3. ADVICE vs AUTOMATION:
   - When a user asks for advice or instructions (e.g. "how do I...", "where can I find..."), treat it as a conversational question ("chat"), never as an automated OS command.
4. SPEAK FIELD RULES:
   - "chat" and "web_search": speak MUST be "" (empty string) because the full answer is synthesized and spoken by the conversational engine.
   - Action tools (open_app, play_youtube, etc.): provide one short spoken sentence confirming the action.
5. SINGLE vs MULTI-ACTION:
   - If the user asks for one thing, return an array with one action object.
   - If the user asks for multiple sequential tasks (e.g. "open notepad and play some music"), return the action objects in sequential order.
6. PASSIVE CONTEXT:
   - [Clipboard Reference] and [Visible Screen Reference] are passive context. Never trigger tools based on passive references unless the user's message explicitly requests action on them.
7. CONFIRMATIONS & CONTEXT:
   - When the user gives a short affirmative confirmation (e.g. "yes", "sure", "proceed", "go ahead") or follow-up reference, resolve the target tool and parameters using [Recent Conversation].

Available tools:
chat {}  — Answering user questions, conversational dialog, general knowledge, reasoning, advice, and explanations.
web_search {"query": "..."}  — Factual inquiries, current events, real-world data, and search queries.
open_website {"url": "https://..."}  — Open a named website, web portal, or URL in the browser.
play_youtube {"query": "..."}  — Search and play music, audio, or video media on YouTube.
show_images {"query": "..."}  — Search and display images online.
get_time {"location": "..."}  — Current time.
get_date {}  — Current date, day of the week, or date and time.
get_weather {"city": "..."}  — Live weather for a city.
open_app {"name": "..."}  — Launch an installed application by name.
open_folder {"name": "..."}  — Open a user directory (downloads, documents, desktop, pictures, music, videos).
find_file {"query": "..."}  — Search files by name, extension, or topic.
type_text {"app": "...", "text": "..."}  — Type or write text into an application.
search_and_type {"text": "..."}  — Type into open search bar and press Enter.
press_key {"keys": "..."}  — Press a keyboard shortcut.
window_management {"action": "..."}  — Window control (minimize_all, close_window, switch_window).
scroll_down {}  — Scroll down.
scroll_up {}  — Scroll up.
new_tab {"url": "..."}  — Open a new browser tab.
close_tab {}  — Close current tab.
next_tab {}  — Switch next tab.
prev_tab {}  — Switch prev tab.
volume_up {}  — Increase volume.
volume_down {}  — Decrease volume.
mute {}  — Mute or unmute audio.
set_volume {"level": "..."}  — Set volume percentage (0-100).
set_brightness {"level": "..."}  — Set brightness percentage (0-100).
hardware_metrics {}  — Check computer hardware statistics (CPU load, RAM memory usage, battery percentage).
open_settings {"setting": "..."}  — Open system settings.
set_timer {"duration": "...", "label": "..."}  — Set a countdown timer with duration (e.g. 10s, 5m, 1h) and optional label.
set_reminder {"time": "...", "message": "..."}  — Set a scheduled reminder.
list_reminders {}  — List all active timers and upcoming reminders.
cancel_reminder {"id": "..."}  — Cancel active timers or reminders.
read_screen {}  — Read all visible text on the active screen upon explicit request.
ask_about_screen {"question": "..."}  — Inspect active screen or window to answer visual questions, explain code/errors, or summarize visible content.
take_screenshot {}  — Capture screen upon explicit request.
lock_pc {}  — Lock the PC upon explicit request.
sleep_pc {}  — Sleep the PC upon explicit request.
empty_recycle_bin {}  — Empty the Recycle Bin upon explicit request.
restart_pc {}  — Restart the PC upon explicit request.
cancel_shutdown {}  — Cancel restart or shutdown.
pause_media {}  — Pause currently playing media.
play_media {}  — Resume or unpause paused media.
next_track {}  — Next song or track.
prev_track {}  — Previous song or track.
wikipedia {"query": "..."}  — Wikipedia topic summary.
calculate {"expression": "..."}  — Math calculation.
exit {}  — Quit assistant.

Examples:
User: "hello how are you" -> [{"tool": "chat", "params": {}, "speak": ""}]
User: "how do airplanes fly" -> [{"tool": "chat", "params": {}, "speak": ""}]
User: "who is the prime minister of Canada" -> [{"tool": "web_search", "params": {"query": "current prime minister of Canada"}, "speak": ""}]
User: "what was the score of the latest game" -> [{"tool": "web_search", "params": {"query": "latest game score"}, "speak": ""}]
User: "open calculator" -> [{"tool": "open_app", "params": {"name": "calculator"}, "speak": "Opening Calculator."}]
User: "play some lofi beats" -> [{"tool": "play_youtube", "params": {"query": "lofi beats"}, "speak": "Playing some lofi beats."}]
User: "open notepad and write a shopping list" -> [{"tool": "open_app", "params": {"name": "notepad"}, "speak": "Opening Notepad."}, {"tool": "chat", "params": {}, "speak": ""}]
"""


# ---------------------------------------------------------------------------
# TTS Text Cleaning Utilities
# ---------------------------------------------------------------------------

def strip_markdown_for_tts(text: str) -> str:
    """
    Strips markdown formatting and unpronounceable code syntax so TTS
    speaks naturally without reading asterisks, braces, quotes, or code symbols aloud.
    """
    if not text:
        return ""
    # Expand currency symbols to spoken words for natural pricing output
    text = text.replace("₹", " rupees ").replace("$", " dollars ").replace("€", " euros ").replace("£", " pounds ")
    # Strip code fences and blocks
    text = re.sub(r"```[a-zA-Z]*\n?([\s\S]*?)```", r"\1", text)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Sanitize escaped quotes, backslashes, and raw code braces for natural speech
    text = re.sub(r'\\"', '"', text)
    text = re.sub(r"\\'", "'", text)
    text = re.sub(r"[{}\[\]\\<>]", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n+", " ", text).strip()
    return text


def clean_tts_text(text: str) -> str:
    """
    Removes characters that cause TTS engines to choke or produce
    garbled output, while preserving normal Unicode letters/words.
    """
    if not text:
        return ""
    result = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith(("L", "N", "P", "Z", "M")) or ch in (
            " ", ",", ".", "!", "?", ":", ";", "-", "'", '"',
        ):
            result.append(ch)
    return "".join(result).strip()


def sanitize_for_tts(text: str) -> str:
    """
    Combined helper: strips markdown then removes unspoken symbols.
    Use this everywhere instead of calling both functions separately.
    """
    return clean_tts_text(strip_markdown_for_tts(text))


# ---------------------------------------------------------------------------
# Qwen 2.5 ChatML Prompt Formatter
# ---------------------------------------------------------------------------

def format_chat_prompt(system_prompt: str, user_content: str | list[dict], model_key: str | None = None) -> tuple[str, list[str]]:
    """Format chat prompt with native ChatML special tokens for Qwen 2.5."""
    if isinstance(user_content, list):
        formatted = f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        for msg in user_content:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                continue
            formatted += f"<|im_start|>{role}\n{content}<|im_end|>\n"
        formatted += "<|im_start|>assistant\n"
        return formatted, STOP_TOKENS

    formatted = (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{user_content}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    return formatted, STOP_TOKENS


def get_active_model_info() -> dict:
    """Return metadata for the currently active model."""
    cfg = get_current_model_config()
    model_path = os.path.join(MODEL_DIR, cfg["filename"])
    return {
        "key": cfg["key"],
        "name": cfg["name"],
        "filename": cfg["filename"],
        "path": model_path,
        "downloaded": os.path.exists(model_path) and os.path.getsize(model_path) > 500_000_000,
        "size_gb": cfg.get("size_gb", 2.05),
    }


def get_available_models() -> dict:
    """Return dictionary of available models and their download status."""
    result = {}
    for key, cfg in AVAILABLE_MODELS.items():
        m_path = os.path.join(MODEL_DIR, cfg["filename"])
        result[key] = {
            "key": key,
            "name": cfg["name"],
            "filename": cfg["filename"],
            "path": m_path,
            "downloaded": os.path.exists(m_path) and os.path.getsize(m_path) > 500_000_000,
            "size_gb": cfg.get("size_gb", 2.0),
        }
    return result


def set_active_model(model_key: str = "qwen-2.5-3b") -> bool:
    """Set or reload the active local model."""
    global _active_model_key, MODEL_NAME, MODEL_FILENAME, MODEL_PATH, MODEL_TARGETS, _local_llm_instance
    if model_key not in AVAILABLE_MODELS:
        logger.warning(f"Unknown model key '{model_key}'. Falling back to 'qwen-2.5-3b'.")
        model_key = "qwen-2.5-3b"

    _active_model_key = model_key
    cfg = AVAILABLE_MODELS[_active_model_key]
    MODEL_NAME = cfg["name"]
    MODEL_FILENAME = cfg["filename"]
    MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)
    MODEL_TARGETS = cfg["targets"]

    with _llm_lock:
        _local_llm_instance = None

    init_local_llm(force_reload=True)
    return True


# ---------------------------------------------------------------------------
# Model Download & Initialization
# ---------------------------------------------------------------------------

def get_model_path(model_key: str | None = None) -> str:
    """Ensure active or requested model weights exist; download safely via huggingface_hub if missing."""
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR, exist_ok=True)

    cfg = AVAILABLE_MODELS.get(model_key or _active_model_key, get_current_model_config())
    target_path = os.path.join(MODEL_DIR, cfg["filename"])

    if os.path.exists(target_path) and os.path.getsize(target_path) > 500_000_000:
        return target_path

    logger.info(f"Downloading {cfg['name']} ({cfg['filename']}, ~{cfg.get('size_gb', 2.0)} GB)… this may take a while.")

    for repo, fname in cfg.get("targets", []):
        try:
            from huggingface_hub import hf_hub_download

            logger.info(f"Trying download from: {repo} ({fname})…")
            downloaded_file = hf_hub_download(
                repo_id=repo, filename=fname, local_dir=MODEL_DIR
            )
            if (
                os.path.exists(downloaded_file)
                and os.path.getsize(downloaded_file) > 500_000_000
            ):
                logger.info(f"Successfully downloaded {cfg['name']} to {downloaded_file}")
                return downloaded_file
        except Exception as e:
            logger.warning(f"Download attempt from {repo} failed: {e}")
            time.sleep(1)

    return target_path


def init_local_llm(force_reload: bool = False):
    """
    Initializes Qwen 2.5 3B AI model instance via llama-cpp-python.
    n_gpu_layers=-1 offloads ALL layers to GPU for maximum speed.
    Thread-safe: uses a lock so concurrent callers don't double-load.
    """
    global _local_llm_instance

    if not force_reload and _local_llm_instance is not None:
        return _local_llm_instance

    with _llm_lock:
        if not force_reload and _local_llm_instance is not None:
            return _local_llm_instance

        _local_llm_instance = None
        model_file = get_model_path()

        if os.path.exists(model_file):
            try:
                from llama_cpp import Llama

                _old_stderr = sys.stderr
                sys.stderr = _devnull
                try:
                    for fa in [True, False]:
                        try:
                            _local_llm_instance = Llama(
                                model_path=model_file,
                                n_ctx=4096,             # 4096 context window for rich conversation history & tool specs
                                n_gpu_layers=-1,        # Offload ALL layers to GPU
                                main_gpu=0,             # Dedicated Discrete GPU (NVIDIA RTX)
                                n_threads=max(1, multiprocessing.cpu_count() - 2),
                                n_batch=1024,           # Faster prompt ingestion & prefill on GPU
                                n_ubatch=512,           # High-throughput physical batch size
                                flash_attn=fa,          # Prioritizes FlashAttention-2 when available
                                use_mmap=True,          # Prevents paging delays
                                verbose=False,
                            )
                            if _local_llm_instance is not None:
                                break
                        except Exception as e_inner:
                            logger.debug(f"[Local AI Engine] Attempt with flash_attn={fa} failed: {e_inner}")
                finally:
                    sys.stderr = _old_stderr

                if _local_llm_instance is not None:
                    logger.info(f"[Local AI Engine] {MODEL_NAME} loaded successfully.")
                    return _local_llm_instance
            except Exception as e_llama:
                logger.error(f"[Local AI Engine] llama-cpp-python load error: {e_llama}")

    return None


def _prepare_chat_messages(system_prompt: str, prompt: str | list[dict]) -> list[dict]:
    """Helper to convert prompt into standard messages list for create_chat_completion."""
    if isinstance(prompt, list):
        if prompt and prompt[0].get("role") == "system":
            return prompt
        return [{"role": "system", "content": system_prompt}] + prompt
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": str(prompt)},
    ]


def query_local_llm(
    prompt: str | list[dict],
    system_prompt: str = (
        "You are Amigo, a helpful voice assistant. "
        "Give clear, direct, spoken answers. No bullet points, no markdown, no asterisks. "
        "Speak in plain sentences."
    ),
    max_tokens: int = 512,
) -> str:
    """
    Queries the local Qwen 2.5 3B Instruct model via llama-cpp-python.
    Supports either a plain string or a list of multi-turn chat messages.
    Returns TTS-ready plain text — no markdown, no emoji.
    """
    llm = init_local_llm()

    if llm is not None:
        try:
            output = ""

            if hasattr(llm, "create_chat_completion"):
                try:
                    chat_messages = _prepare_chat_messages(system_prompt, prompt)
                    res = llm.create_chat_completion(
                        messages=chat_messages,
                        max_tokens=max_tokens,
                        temperature=0.7,
                        stop=STOP_TOKENS,
                    )
                    output = res["choices"][0]["message"]["content"]
                except Exception as chat_err:
                    logger.debug(f"[Local AI Engine] Chat completion note: {chat_err}")
                    output = ""

            if not output and (
                hasattr(llm, "create_completion") or hasattr(llm, "__call__")
            ):
                full_prompt, stop_toks = format_chat_prompt(system_prompt, prompt)

                if hasattr(llm, "create_completion"):
                    res = llm.create_completion(
                        full_prompt, max_tokens=max_tokens, stop=stop_toks, temperature=0.7
                    )
                    output = res["choices"][0]["text"]
                else:
                    res = llm(full_prompt, max_tokens=max_tokens, stop=stop_toks, temperature=0.7)
                    if isinstance(res, dict) and "choices" in res:
                        output = res["choices"][0]["text"]
                    elif isinstance(res, str):
                        output = res
                    else:
                        output = str(res)

            if isinstance(output, str) and output.strip():
                for tok in ["<|im_end|>", "<|im_start|>", "<|endoftext|>", "<|eot_id|>", "<|end_of_text|>", "<|eom_id|>", "<|start_header_id|>", "<end_of_turn>", "[/INST]", "</s>"]:
                    output = output.split(tok)[0]
                output = output.strip()
                clean_res = sanitize_for_tts(output)
                if clean_res:
                    return clean_res

        except Exception as e:
            logger.error(f"[Local AI Engine] Generation error: {e}")

    return "I am here and ready to help. Please try asking me again."


def query_local_llm_stream(
    prompt: str | list[dict],
    system_prompt: str = (
        "You are Amigo, a helpful voice assistant. "
        "Give clear, direct, spoken answers. No bullet points, no markdown, no asterisks. "
        "Speak in plain sentences."
    ),
    max_tokens: int = 512,
    interruption_event: threading.Event | None = None,
):
    """
    Streams raw tokens from local Qwen 2.5 3B Instruct in real-time.
    Supports either a plain string or a list of multi-turn chat messages.
    Yields string tokens. Automatically terminates when interruption_event is set.
    """
    llm = init_local_llm()

    if llm is not None:
        try:
            if hasattr(llm, "create_chat_completion"):
                try:
                    chat_messages = _prepare_chat_messages(system_prompt, prompt)
                    stream_res = llm.create_chat_completion(
                        messages=chat_messages,
                        max_tokens=max_tokens,
                        temperature=0.7,
                        stop=STOP_TOKENS,
                        stream=True,
                    )
                    for chunk in stream_res:
                        if interruption_event and interruption_event.is_set():
                            return
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            yield token
                    return
                except Exception as chat_err:
                    logger.debug(f"[Local AI Engine] Streaming chat completion note: {chat_err}")

            full_prompt, stop_toks = format_chat_prompt(system_prompt, prompt)
            if hasattr(llm, "create_completion"):
                stream_res = llm.create_completion(
                    full_prompt, max_tokens=max_tokens, stop=stop_toks, temperature=0.7, stream=True
                )
                for chunk in stream_res:
                    if interruption_event and interruption_event.is_set():
                        return
                    token = chunk["choices"][0].get("text", "")
                    if token:
                        yield token
                return

        except Exception as e:
            logger.error(f"[Local AI Engine] Streaming error: {e}")
            yield "I am processing that. Please try asking me again."
    else:
        yield "I am here and ready to help."



def _split_long_chunk(text: str, max_chars: int = 240) -> list[str]:
    """Splits a long text chunk at natural clause or word boundaries to prevent TTS phoneme overflow."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    rem = text
    while len(rem) > max_chars:
        # Find best split point on sentence/clause boundary punctuation
        split_idx = -1
        for sep in [", ", "; ", ": ", " - ", " "]:
            pos = rem.rfind(sep, 0, max_chars)
            if pos > 40:
                split_idx = pos + len(sep)
                break
        if split_idx <= 0:
            split_idx = max_chars
        part = rem[:split_idx].strip()
        if part:
            chunks.append(part)
        rem = rem[split_idx:].strip()
    if rem:
        chunks.append(rem)
    return chunks


def stream_sentence_chunks(token_generator, interruption_event: threading.Event | None = None):
    """
    Buffers token stream and yields complete, natural sentence chunks
    as soon as sentence boundary punctuation is detected for instant TTS.
    Enforces maximum chunk size to prevent Kokoro phoneme truncation warnings (>510 phonemes).
    """
    buffer = ""
    sentence_split_regex = re.compile(r'(?<=[.!?])\s+|\n+')
    max_chunk_chars = 240

    for token in token_generator:
        if interruption_event and interruption_event.is_set():
            return

        buffer += token

        # Strip special stop tokens if they bleed through
        for tok in ["<|im_end|>", "<|im_start|>", "<|endoftext|>", "<|eot_id|>", "<|end_of_text|>", "<|eom_id|>", "<|start_header_id|>", "<end_of_turn>", "[/INST]", "</s>"]:
            if tok in buffer:
                buffer = buffer.split(tok)[0]

        # 1. Check if we have one or more complete sentences
        splits = sentence_split_regex.split(buffer)
        if len(splits) > 1:
            for candidate in splits[:-1]:
                if interruption_event and interruption_event.is_set():
                    return
                cand_stripped = candidate.strip()
                if not cand_stripped:
                    continue
                # Skip false splits on common abbreviations
                if re.search(r'\b(mr|mrs|ms|dr|vs|eg|ie|etc)\.$', cand_stripped, re.IGNORECASE):
                    continue
                clean_chunk = sanitize_for_tts(cand_stripped)
                if clean_chunk:
                    for sub in _split_long_chunk(clean_chunk, max_chunk_chars):
                        yield sub
            buffer = splits[-1]

        # 2. Safety: If buffer grows too long without period punctuation (e.g. JSON or unpunctuated text)
        elif len(buffer) > max_chunk_chars:
            split_idx = -1
            for sep in [", ", "; ", ": ", " - ", " "]:
                pos = buffer.rfind(sep, 0, max_chunk_chars)
                if pos > 40:
                    split_idx = pos + len(sep)
                    break
            if split_idx > 0:
                head = buffer[:split_idx].strip()
                buffer = buffer[split_idx:].strip()
                clean_chunk = sanitize_for_tts(head)
                if clean_chunk:
                    for sub in _split_long_chunk(clean_chunk, max_chunk_chars):
                        yield sub

    if buffer.strip() and not (interruption_event and interruption_event.is_set()):
        clean_chunk = sanitize_for_tts(buffer.strip())
        if clean_chunk:
            for sub in _split_long_chunk(clean_chunk, max_chunk_chars):
                yield sub



# ---------------------------------------------------------------------------
# Intent normalization
# ---------------------------------------------------------------------------

def clean_spoken_query(raw_query: str) -> str:
    """
    Strips spoken natural language filler words to normalize
    voice inputs for accurate intent classification.
    """
    if not raw_query:
        return ""

    text = raw_query.lower().strip()

    prefixes = [
        r"^hey amigo\b",
        r"^hi amigo\b",
        r"^hello amigo\b",
        r"^amigo\b",
        r"^could you please\b",
        r"^can you please\b",
        r"^would you please\b",
        r"^could you\b",
        r"^can you\b",
        r"^would you\b",
        r"^please\b",
        r"^i want to\b",
        r"^i would like to\b",
        r"^tell me\b",
        r"^kindly\b",
    ]
    for p in prefixes:
        text = re.sub(p, "", text).strip()

    suffixes = [r"\bplease$", r"\bfor me$", r"\bboss$", r"\bamigo$"]
    for s in suffixes:
        text = re.sub(s, "", text).strip()

    return text if text else raw_query.lower().strip()


# ---------------------------------------------------------------------------
# Pre-compiled fast-path regex patterns (saves ~2ms per call)
# ---------------------------------------------------------------------------
_RE_EXIT      = re.compile(r"\b(goodbye|good night|exit|bye)\b|^\s*sleep\s*$")
_RE_SHUTDOWN  = re.compile(r"\b(shutdown|turn off computer|turn off pc|power off)\b")
_RE_VOL_UP    = re.compile(r"\b(volume up|increase volume|turn up|louder)\b")
_RE_VOL_DOWN  = re.compile(r"\b(volume down|decrease volume|turn down|quieter)\b")
_RE_MUTE      = re.compile(r"\b(mute|unmute|silence)\b")
_RE_PAUSE     = re.compile(r"\b(pause video|pause song|pause music|pause|stop music|stop video)\b")
_RE_RESUME    = re.compile(r"\b(resume music|resume song|resume video|resume playback|continue music|continue song|unpause)\b|^\s*(resume|continue)\s*$", re.IGNORECASE)
_RE_NEXT_TRACK = re.compile(r"\b(next track|next song|next video|skip song|skip track|skip video|play next|next)\b", re.IGNORECASE)
_RE_PREV_TRACK = re.compile(r"\b(previous track|previous song|previous video|prev track|prev song|prev video|go back song|last song|play previous|replay song)\b", re.IGNORECASE)
_RE_PLAY_CHECK = re.compile(r"play\s+([a-zA-Z0-9\s]+)")
_RE_SCREENSHOT  = re.compile(r"\b(screenshot|capture screen|screen shot)\b")
_RE_READ_SCREEN = re.compile(r"\b(read (?:my )?screen|screen vision|what(?:'s| is) on my screen|explain (?:my |the )?screen|what error is (?:this|on screen)|summarize (?:my |the )?screen)\b", re.IGNORECASE)
_RE_SHOW_IMAGES = re.compile(
    r"\b(show (me )?(the |some )?(pictures?|images?|photos?)|open (google )?images?|i want to see (the )?(pictures?|images?|photos?))\b",
    re.IGNORECASE,
)
_RE_MINIMIZE  = re.compile(r"\b(minimize all|hide all windows|show desktop)\b")
_RE_CLOSE     = re.compile(r"\b(close this|close window|close app)\b")
_RE_SWITCH    = re.compile(r"\b(switch window|alt tab)\b")
_RE_TIME = re.compile(
    r"\b(what time is it|time right now|current time|what is the time"
    r"|what'?s the time|tell me the time|time please|time now|^\s*time\s*$)\b",
    re.IGNORECASE,
)
_RE_DATE = re.compile(
    r"\b(what('?s| is) (today'?s? date|tomorrow'?s? date|yesterday'?s? date|the date|the current date)"
    r"|today'?s date|tomorrow'?s date|yesterday'?s date|current date|what day is it"
    r"|what day is tomorrow|what date is tomorrow|what day was yesterday|what date was yesterday"
    r"|^\s*date\s*$|^\s*what('?s| is) (the )?date\s*$|^\s*today'?s? date\s*$|what('?s| is) (the )?date and time"
    r"|date and time|current date and time)\b",
    re.IGNORECASE,
)
_RE_CALC      = re.compile(r"\b(calculate|math|solve|what is \d|what'?s \d+[\+\-\*\/])\b")
_RE_CALC_STRIP = re.compile(r"^(calculate|solve|math|what is|what'?s)\s*")
# General recency/currency signal — NOT domain-specific.
# Catches queries whose answer is time-sensitive by detecting temporal markers
# and question structures that ask about the current state of the world.
# This avoids hardcoding any specific job titles, names, or topics.
_RE_RECENCY_SIGNAL = re.compile(
    r"\b(current(ly)?|latest|right now|as of (?:now|today)|today'?s?|recent(ly)?|nowadays|"
    r"at (?:the )?moment|this (?:year|month|week)|who(?:'?s| is) the |what(?:'?s| is) the (?:current|latest|new)|"
    r"who (?:is |are )?(?:currently |now |still )?(?:the |in |leading |running |heading |serving )|"
    r"what (?:is |are )?(?:currently |now |still )?(?:the |happening |going on)|"
    r"who (?:won|leads|became|got|was (?:elected|appointed|chosen|named))|"
    r"what (?:happened|changed|is new|is the update|is the status)|"
    r"is .{1,40} still|when did .{1,40} (?:happen|start|end|change))\b",
    re.IGNORECASE,
)
_RE_SET_TIMER = re.compile(
    r"\b(?:set\s+(?:a\s+)?)?(?:timer\s+(?:for\s+)?|(\d+[\w\s]*)\s+timer)\s*([a-zA-Z0-9\s]*)",
    re.IGNORECASE,
)
_RE_SET_REMINDER = re.compile(
    r"\b(?:remind me(?: (?:to|about))?|set (?:an? )?(?:reminder|alarm)(?: (?:to|for|about))?|wake me up(?: (?:at|in))?)(?:\s+(.+))?",
    re.IGNORECASE,
)
_RE_SET_TIMER = re.compile(
    r"\b(?:set|start|create|run|launch|open)?\s*(?:a\s*)?(?:timer|countdown|stopwatch)\b|\b(?:timer|countdown|stopwatch)\s+(?:for\s+)?(\d+)\s*(m|min|mins|minute|minutes|s|sec|secs|second|seconds|h|hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)
_RE_SET_REMINDER = re.compile(
    r"\b(?:set|create|add|make|schedule)\s*(?:a\s*)?reminder\b",
    re.IGNORECASE,
)
_RE_LIST_REMINDERS = re.compile(
    r"\b(?:what are my|list|show|get|active)\s+(?:all\s+|my\s+)?(?:timers?|reminders?)\b|\b(?:my\s+)?(?:timers?|reminders?)\s+status\b",
    re.IGNORECASE,
)
_RE_CANCEL_REMINDER = re.compile(
    r"\b(?:cancel|stop|delete|clear)\s+(?:all\s+|my\s+)?(?:timers?|reminders?)\b",
    re.IGNORECASE,
)
_RE_FAVORITE  = re.compile(r"\b(tired|favourite|favorite song|my playlist)\b")
_RE_PLAY_STRIP = re.compile(r"^(play|listen to|listen|put on|stream|watch)\s*")
_RE_OPEN_FOLDER = re.compile(
    r"\b(?:open|show|go to)\s+(?:the\s+|my\s+)?(downloads?|desktop|documents?|pictures?|photos?|videos?|music)\s*(?:folder|directory)?\b|"
    r"\b(?:open|show)\s+(?:the\s+|my\s+)?folder\s+(downloads?|desktop|documents?|pictures?|photos?|videos?|music)\b",
    re.IGNORECASE,
)
# _RE_FIND_FILE: Only fires when the query explicitly mentions a file type,
# file extension, or clear file-search vocabulary alongside a search verb.
# This avoids false matches on app-open commands like "find discord" or "get notepad".
_RE_FIND_FILE = re.compile(
    r"\b(find|search|where is|locate|look for)\b"
    r".*"
    r"\b(files?|pdf|docx?|xlsx?|pptx?|txt|csv|json|xml|py|js|html|css|mp3|mp4|mkv|zip|rar"
    r"|document|script|code|notes?|resume|cv|report|spreadsheet|presentation|image|photo|video|audio)\b",
    re.IGNORECASE,
)

# App-like single words that should NEVER route to find_file.
# Built dynamically — no hardcoded app list, just a heuristic: short single-word
# queries that don't mention any file-type word belong to open_app.
_RE_APP_OPEN_ONLY = re.compile(
    r"^(open|launch|start|run)\s+[\w\s]{1,30}$",
    re.IGNORECASE,
)
_RE_YT_FILLER  = re.compile(
    r"\s+(i (like|love|enjoy|really like|really love) (this|that|it)(\s+so much|\s+a lot|\s+too)?|so much|a lot|for me|please|right now|now)$",
    re.IGNORECASE,
)


def _clean_yt_query(raw: str) -> str:
    """Strip spoken filler before/after a song or video query."""
    if not raw:
        return ""
    cleaned = re.sub(
        r"^(?:open\s+(?:youtube|yt)\s+(?:and\s+|to\s+)?(?:let'?s\s+)?(?:play\s+)?|play\s+|listen\s+to\s+|put\s+on\s+|stream\s+|watch\s+)",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = _RE_YT_FILLER.sub("", cleaned).strip()
    return cleaned if cleaned else raw

# YouTube / music fast-path — only unambiguous "play X" patterns
_RE_PLAY_MUSIC = re.compile(
    r"^(play|listen to|put on|stream|watch)\s+(?!video$|song$|music$).+"
    r"|play (some |a |my )?(music|songs?|playlist|beats?|lofi|jazz|pop|rock|hip hop|bollywood|classical)"
)

# NOTE: _RE_WIKI and _RE_WEB_SEARCH have been intentionally removed.
# 'what is X', 'who is X', 'look up X' are nuanced — the LLM must decide
# whether to answer directly (chat) or use wikipedia/web_search.
# Hard-coding these to Wikipedia/Google is wrong and ruins conversational flow.

_RE_OPEN          = re.compile(r"\b(open|launch)\s+(.+)")
_RE_SYSTEM_STATUS = re.compile(r"\b(system status|cpu (?:usage|percent|load|status)|ram (?:usage|percent|load|status)|memory usage|how much ram|battery (?:status|level|percent))\b", re.IGNORECASE)
_RE_BRIGHTNESS    = re.compile(r"\b(brightness|display)\b")
_RE_SETTINGS      = re.compile(
    r"^(?:open|launch|show|go to|take me to|navigate to|change|adjust|toggle)\s+"
    r"(?:the\s+)?(?:windows\s+|my\s+)?"
    r"(?:(?:a|an)\s+)?(?:wifi|wi-fi|bluetooth|network|internet|settings|random windows setting|random"
    r"|display|brightness|wallpaper|background|dark mode|night light|color|theme|personalization"
    r"|sound|volume|notifications?|battery|power|sleep|mouse|keyboard|touchpad|pen|clipboard"
    r"|privacy|location|camera|microphone|mic|webcam|storage|disk|startup|default apps"
    r"|language|region|date|time|accounts|sign.in|password|pin"
    r"|security|defender|antivirus|update|windows update|updates"
    r"|game|gaming|game mode|accessibility|magnifier|narrator"
    r"|recovery|troubleshoot|taskbar|lock screen|vpn|about)"
    r"(?:\s+(?:settings?|page|panel|menu|window))?$",
    re.IGNORECASE,
)
_RE_SET_BRIGHTNESS = re.compile(
    r"\b(?:set|increase|decrease|change|put|adjust|turn(?:\s+up|\s+down)?|make)?\s*(?:the\s+)?(?:screen\s+|display\s+)?brightness\s*(?:to\s+|\s+)?(\d{1,3})\s*%?",
    re.IGNORECASE,
)
_RE_SET_VOLUME = re.compile(
    r"\b(?:set|increase|decrease|change|put|adjust|turn(?:\s+up|\s+down)?|make)?\s*(?:the\s+)?(?:system\s+|audio\s+|sound\s+)?volume\s*(?:to\s+|\s+)?(\d{1,3})\s*%?",
    re.IGNORECASE,
)
_RE_LOCK_PC        = re.compile(r"\b(lock (?:my )?(?:pc|computer|screen|workstation))\b", re.IGNORECASE)
_RE_SLEEP_PC       = re.compile(r"\b(sleep (?:pc|computer)|put (?:pc|computer|system) to sleep|go to sleep)\b", re.IGNORECASE)
_RE_EMPTY_RECYCLE  = re.compile(r"\b(empty (?:the )?(?:recycle bin|trash)|clear (?:the )?(?:recycle bin|trash))\b", re.IGNORECASE)
_RE_RESTART_PC     = re.compile(r"\b(restart (?:my )?(?:pc|computer|system)|reboot (?:my )?(?:pc|computer|system))\b", re.IGNORECASE)
_RE_CANCEL_SHUTDOWN = re.compile(r"\b(cancel (?:shutdown|restart|reboot)|abort (?:shutdown|restart|reboot))\b", re.IGNORECASE)
_RE_OPEN_AND_TYPE   = re.compile(
    r"\bopen\s+([a-zA-Z0-9\s\.\-_]+?)\s+(?:and|then|to)\s+(?:type|paste|write)\b",
    re.IGNORECASE,
)
_RE_PASTE_INTO_APP = re.compile(
    r"\b(?:paste|type|write|put|insert|copy|pace|page)\s+(?:.*?)\s*(?:in|into|to|on)\s+(?:my\s+)?([a-zA-Z0-9\s\.\-_]+?)$",
    re.IGNORECASE,
)
_RE_TYPE_TEXT      = re.compile(
    r"\b(paste (?:that|this|it|the email|the text|the message)?|type (?:that|this|it|out)?|write (?:that|this|it) down)\b",
    re.IGNORECASE,
)
# -----------------------------------------------------------------------
# Probe Guard — catches adversarial, introspective, and prompt-injection
# queries before they reach the tool dispatcher. All patterns are
# intent-based (question structure) not keyword lists, so new variants
# are covered without adding more entries.
# -----------------------------------------------------------------------
_RE_PROBE_GUARD = re.compile(
    r"\b("
    # Capability / tool enumeration probes
    r"what (?:tools?|functions?|api[s]?|capabilities|features|method[s]?|command[s]?) (?:do you have|can you (?:use|execute|access|call|run)|are available|do you (?:possess|use|have))"
    r"|what (?:tools?|functions?|api[s]?|method[s]?|command[s]?)\s*(?:(?:and|or|,)\s*(?:tools?|functions?|api[s]?|method[s]?|command[s]?)\s*)+(?:do you have|can you (?:use|access|execute|call)|are (?:available|accessible))"
    r"|(?:what|which) (?:functions?|api[s]?|method[s]?) (?:do you have|are (?:available|accessible)|can you (?:use|call|access|execute))"
    r"|list (?:your|all|the) (?:tools?|functions?|api[s]?|capabilities|features|command[s]?)"
    r"|what can you (?:do|execute|access|call|run)"
    r"|show (?:me )?(?:your|all) (?:tools?|functions?|api[s]?|capabilities|command[s]?)"
    # Internal JSON / schema / prompt extraction probes
    r"|output (?:your|the) (?:raw|internal|debug|system|agent|hidden|private)"
    r"|(?:give|show|print|display|dump|output|return|write|produce) (?:me )?(?:your |the )?(?:raw|internal|debug|system|agent|json|schema|prompt|decision|template|struct|payload|response json|action json|tool json|format|response format|output format)"
    r"|what (?:is|are) (?:your|the) (?:raw|internal|debug|system|agent|json|schema|prompt|decision|template|instruction[s]?)"
    r"|(?:show|reveal|expose|leak|print|dump|read|output) (?:your )?(?:system |agent |hidden |internal |private |raw )?(?:prompt|instruction[s]?|context|template|schema|json|payload|rules?|config)"
    # Development / architecture probes
    r"|how (?:are|were|is|was) you (?:built|made|trained|developed|programmed|designed|created|coded|implemented|fine.?tuned)"
    r"|what (?:model|llm|ai|neural network|architecture|framework|engine) (?:are you|do you use|powers you|is behind you|runs you)"
    r"|(?:what is|tell me|explain) (?:your|the) (?:working|architecture|model|development|training|weights|technology|backend|stack)"
    r"|who (?:built|made|trained|developed|programmed|designed|created|coded) you"
    r"|what (?:version|release|checkpoint|weights) (?:are you|do you run|of \w+ are you)"
    # Memory / secret probes
    r"|tell me (?:a )?(?:secret|hidden|private|confidential) (?:memory|fact|data|info|detail)"
    r"|what (?:secret|hidden|private|confidential) (?:memory|memories|fact[s]?|data|info|detail[s]?) do you (?:have|store|hold|keep|remember)"
    r"|(?:reveal|expose|leak|share|output) (?:your )?(?:secret|hidden|private|confidential|stored) (?:memory|memories|data|fact[s]?)"
    r")\b",
    re.IGNORECASE,
)


def parse_user_intent_with_llm(user_query: str) -> dict[str, Any]:
    """
    Parses natural voice queries into intent actions and parameters.
    Uses pre-compiled regex patterns for faster matching.
    Returns GENERAL_CHAT when no specific intent is detected (LLM fallback).
    """
    if not user_query:
        return {"intent": "GENERAL_CHAT", "params": {"query": ""}}

    text = clean_spoken_query(user_query)

    # 0. Probe/adversarial guard — introspective, capability, JSON-extraction,
    #    secret-memory, and development questions all route to conversational chat.
    #    The guardrailed voice prompt handles the response naturally.
    if _RE_PROBE_GUARD.search(text) or re.search(
        r"^(?:what method|how do you|explain how|how does|why do you|who are you|"
        r"are you (?:an? )?(?:ai|bot|robot|machine|program|model|llm)|"
        r"what (?:are you|is amigo))\b",
        text, re.IGNORECASE,
    ):
        return {"intent": "GENERAL_CHAT", "params": {"query": user_query}}

    # 1. Exit / Shutdown / Lock / Sleep
    if _RE_CANCEL_SHUTDOWN.search(text):
        return {"intent": "CANCEL_SHUTDOWN", "params": {}}
    if _RE_EXIT.search(text):
        return {"intent": "EXIT", "params": {}}
    if _RE_SHUTDOWN.search(text):
        return {"intent": "SHUTDOWN", "params": {}}
    if _RE_LOCK_PC.search(text):
        return {"intent": "LOCK_PC", "params": {}}
    if _RE_SLEEP_PC.search(text):
        return {"intent": "SLEEP_PC", "params": {}}
    if _RE_EMPTY_RECYCLE.search(text):
        return {"intent": "EMPTY_RECYCLE_BIN", "params": {}}
    if _RE_RESTART_PC.search(text):
        return {"intent": "RESTART_PC", "params": {}}

    # 1b. Short direct Paste/Type shortcuts (e.g. "open notepad and paste that", "paste that email into notepad")
    if len(text) < 80:
        open_type_m = _RE_OPEN_AND_TYPE.search(text)
        if open_type_m:
            app = open_type_m.group(1).strip()
            return {"intent": "TYPE_TEXT", "params": {"app": app, "text": ""}}

        paste_into_m = _RE_PASTE_INTO_APP.search(text)
        if paste_into_m:
            app = paste_into_m.group(1).strip()
            if app in ("here", "active window", "document", "file", "it", "this"):
                app = ""
            return {"intent": "TYPE_TEXT", "params": {"app": app, "text": ""}}

        if _RE_TYPE_TEXT.search(text):
            return {"intent": "TYPE_TEXT", "params": {"app": "", "text": ""}}

    # 1c. Direct Brightness & Hardware Controls
    bright_match = _RE_SET_BRIGHTNESS.search(text)
    if bright_match:
        return {"intent": "SET_BRIGHTNESS", "params": {"level": bright_match.group(1)}}

    # 1d. Direct Volume Controls
    vol_match = _RE_SET_VOLUME.search(text)
    if vol_match:
        return {"intent": "SET_VOLUME", "params": {"level": vol_match.group(1)}}

    # 2. System Settings opener — pass full descriptive phrase to the resolver
    if _RE_SETTINGS.search(text):
        # Strip action verbs so only the setting description remains
        setting_phrase = re.sub(
            r"^(?:open|launch|show|go to|take me to|navigate to)\s+(?:the\s+)?(?:windows\s+)?",
            "", text, flags=re.IGNORECASE
        ).strip()
        # Remove trailing noise words
        setting_phrase = re.sub(r"\s*(?:settings?|page|panel|menu|window)$", "", setting_phrase, flags=re.IGNORECASE).strip()
        return {"intent": "OPEN_SETTINGS", "params": {"setting": setting_phrase}}

    # 2b. Special Folder opener (High Priority — before file finder)
    folder_m = _RE_OPEN_FOLDER.search(text)
    if folder_m:
        folder_name = (folder_m.group(1) or folder_m.group(2) or "").strip().lower()
        if folder_name:
            return {"intent": "OPEN_FOLDER", "params": {"name": folder_name}}

    # 2c. App opener (checked BEFORE file finder to avoid "find discord" misroutes)
    #     Only short single-target queries that start with open/launch/start/run.
    if _RE_APP_OPEN_ONLY.search(text) and " and " not in text and len(text) < 50:
        open_m = _RE_OPEN.search(text)
        if open_m:
            target = open_m.group(2).strip()
            if not re.search(
                r"^(https?://|www\.)|\b[a-zA-Z0-9-]+\.(com|org|net|io|co|app|gov|edu|ai|dev)\b",
                target, re.I
            ):
                return {"intent": "OPEN_APP", "params": {"app_name": target}}

    # 2c. File Finder — only when query clearly asks about a file type
    if _RE_FIND_FILE.search(text):
        return {"intent": "FIND_FILE", "params": {"query": user_query}}

    # 3b. Media & Sound Controls
    if _RE_VOL_UP.search(text):
        return {"intent": "MEDIA_CONTROL", "params": {"action": "volume_up"}}
    if _RE_VOL_DOWN.search(text):
        return {"intent": "MEDIA_CONTROL", "params": {"action": "volume_down"}}
    if _RE_MUTE.search(text):
        return {"intent": "MEDIA_CONTROL", "params": {"action": "mute"}}
    if _RE_NEXT_TRACK.search(text):
        return {"intent": "NEXT_TRACK", "params": {}}
    if _RE_PREV_TRACK.search(text):
        return {"intent": "PREV_TRACK", "params": {}}
    if _RE_PAUSE.search(text):
        return {"intent": "MEDIA_CONTROL", "params": {"action": "pause_media"}}
    if _RE_RESUME.search(text) and not _RE_PLAY_CHECK.search(text):
        return {"intent": "MEDIA_CONTROL", "params": {"action": "pause_media"}}

    # 3. Screenshot, Screen Vision & Image Search
    if _RE_SHOW_IMAGES.search(text):
        # Pass to LLM so it can extract the relevant topic from conversation context
        return {"intent": "GENERAL_CHAT", "params": {"query": user_query}}
    if _RE_SCREENSHOT.search(text):
        return {"intent": "SCREENSHOT", "params": {}}
    if _RE_READ_SCREEN.search(text):
        return {"intent": "READ_SCREEN", "params": {}}

    # 4. Window Management
    if _RE_MINIMIZE.search(text):
        return {"intent": "WINDOW_MANAGEMENT", "params": {"action": "minimize_all"}}
    if _RE_CLOSE.search(text):
        return {"intent": "WINDOW_MANAGEMENT", "params": {"action": "close_window"}}
    if _RE_SWITCH.search(text):
        return {"intent": "WINDOW_MANAGEMENT", "params": {"action": "switch_window"}}

    # 5. Time & Date
    if _RE_TIME.search(text) and "weather" not in text:
        return {"intent": "GET_TIME", "params": {}}
    if _RE_DATE.search(text):
        return {"intent": "GET_DATE", "params": {}}

    # 5b. Reminders & Timers
    if _RE_SET_TIMER.search(text):
        m = _RE_SET_TIMER.search(text)
        dur = text
        if m:
            dur = (m.group(1) or m.group(2) or text).strip()
        return {"intent": "SET_TIMER", "params": {"duration": dur, "query": user_query}}
    if _RE_SET_REMINDER.search(text):
        return {"intent": "SET_REMINDER", "params": {"query": user_query}}
    if _RE_LIST_REMINDERS.search(text):
        return {"intent": "LIST_REMINDERS", "params": {}}
    if _RE_CANCEL_REMINDER.search(text):
        return {"intent": "CANCEL_REMINDER", "params": {}}

    # 6. Math & Calculation
    if _RE_CALC.search(text):
        expr = _RE_CALC_STRIP.sub("", text).strip()
        return {"intent": "CALCULATE", "params": {"expression": expr}}

    # 6b. Direct Search Commands (e.g. "search google for health insurance", "google best laptops", "search for X")
    search_m = re.search(
        r"^(?:search\s+(?:on\s+google|google|the\s+web|online|the\s+internet)\s+(?:for\s+)?|google\s+|search\s+for\s+)(.+)$",
        text,
        re.IGNORECASE,
    )
    if search_m:
        sq = search_m.group(1).strip()
        # Clean conversational filler like "u told me na that searching for..."
        sq = re.sub(
            r"^(?:you told me|u told me|you said|u said|na that|that|na)\s*(?:searching for|about|for)?\s*",
            "",
            sq,
            flags=re.IGNORECASE,
        ).strip()
        if sq and len(sq) > 2 and sq.lower() not in ("it", "that", "this", "na", "now"):
            return {"intent": "SEARCH_GOOGLE", "params": {"query": sq}}

    # 7. YouTube / Music (Fast path — avoid LLM for common play requests)
    if _RE_PLAY_MUSIC.search(text):
        query_part = _clean_yt_query(_RE_PLAY_STRIP.sub("", text).strip()) or "popular hit songs playlist"
        return {"intent": "SEARCH_YOUTUBE", "params": {"query": query_part}}

    if _RE_FAVORITE.search(text):
        return {"intent": "PLAY_FAVORITE", "params": {}}

    # 8. Open Application / Web Shortcut (general fallback for all remaining open commands)
    if " and " not in text and len(text) < 50:
        open_m = _RE_OPEN.search(text)
        if open_m:
            target = open_m.group(2).strip()
            if re.search(
                r"^(https?://|www\.)|\b[a-zA-Z0-9-]+\.(com|org|net|io|co|app|gov|edu|ai|dev)\b",
                target, re.I
            ):
                url = target if target.startswith("http") else "https://" + target
                return {"intent": "SEARCH_GOOGLE", "params": {"query": target, "url": url}}
            return {"intent": "OPEN_APP", "params": {"app_name": target}}

    # 10. Weather
    if "weather" in text or "temperature" in text or "climate" in text:
        city = (
            text.replace("weather in", "")
            .replace("weather of", "")
            .replace("weather", "")
            .strip()
        )
        return {"intent": "WEATHER", "params": {"city": city}}

    # 11. Hardware / OS controls
    if _RE_SYSTEM_STATUS.search(text):
        return {"intent": "SYSTEM_STATUS", "params": {}}
    bright_match = re.search(r"brightness.*?(\d+)", text)
    if bright_match:
        return {"intent": "SET_BRIGHTNESS", "params": {"level": bright_match.group(1)}}
    if _RE_BRIGHTNESS.search(text):
        return {"intent": "SET_BRIGHTNESS", "params": {"level": "50"}}
    if _RE_SETTINGS.search(text):
        setting_type = "bluetooth" if "bluetooth" in text else "wifi"
        return {"intent": "OPEN_SETTINGS", "params": {"setting": setting_type}}

    # 12. Recency / currency signal — non-hardcoded catch-all for time-sensitive queries.
    # Detects temporal markers and question structures that ask about the current state
    # of the world, without enumerating any specific domains or job titles.
    # The LLM's training data is stale, so these always benefit from a live search.
    if _RE_RECENCY_SIGNAL.search(text):
        return {"intent": "SEARCH_GOOGLE", "params": {"query": user_query}}

    # Default: let the LLM decide — it knows chat vs wikipedia vs web_search
    return {"intent": "GENERAL_CHAT", "params": {"query": user_query}}


# ---------------------------------------------------------------------------
# Module-level Intent → Agent Action mapping (created once, reused always)
# ---------------------------------------------------------------------------
def _build_intent_map(params: dict, user_query: str) -> dict:
    """Returns the full INTENT_MAP dict with current params and user profile defaults bound in."""
    yt_query = _clean_yt_query(params.get("query", params.get("song", user_query)).strip())
    
    # Check user profile for smart defaults if generic music request
    if not yt_query or yt_query.lower() in ["popular hit songs playlist", "music", "some music", "a song", "songs", ""]:
        try:
            from ai import load_memory
            _prof = load_memory().get("user_profile", {}).get("preferences", {})
            fav_artists = _prof.get("favorite_artists", [])
            fav_genres = _prof.get("favorite_genres", [])
            if fav_artists:
                yt_query = f"{fav_artists[0]} songs playlist"
                yt_msg = f"Playing {fav_artists[0]} for you on YouTube."
            elif fav_genres:
                yt_query = f"{fav_genres[0]} music playlist"
                yt_msg = f"Putting on some {fav_genres[0]} music for you."
            else:
                yt_query = "popular hit songs playlist"
                yt_msg = "Putting on some great music for you right now!"
        except Exception:
            yt_query = "popular hit songs playlist"
            yt_msg = "Putting on some great music for you right now!"
    else:
        yt_msg = f"Sure thing! Playing {yt_query} on YouTube."
        
    app_name = params.get("app_name", "").strip()
    app_msg = f"Opening {app_name} for you right away!" if app_name else "Opening that app for you!"

    city = params.get("city", "").strip()
    if not city:
        try:
            from ai import load_memory
            city = load_memory().get("user_profile", {}).get("preferences", {}).get("favorite_city", "")
        except Exception:
            pass

    return {
        "WIKIPEDIA":     ("wikipedia",    {"query": params.get("query", user_query)},    "Looking that up on Wikipedia."),
        "SEARCH_GOOGLE": ("web_search",   {"query": params.get("query", user_query)},    "Searching Google."),
        "SEARCH_YOUTUBE":("play_youtube",  {"query": yt_query},                           yt_msg),
        "PLAY_MUSIC":    ("play_youtube",  {"query": yt_query},                           yt_msg),
        "SHOW_IMAGES":   ("show_images",   {"query": params.get("query", user_query)},    "Opening Google Images so you can see that!"),
        "PLAY_FAVORITE": ("play_youtube", {"query": yt_query},                           yt_msg),
        "GET_TIME":      ("chat",         {},                                             ""),
        "GET_DATE":      ("chat",         {},                                             ""),
        "CALCULATE":     ("calculate",    {"expression": params.get("expression", "")},  "Calculating."),
        "WEATHER":       ("get_weather",  {"city": city},                                 "Checking weather."),
        "SCREENSHOT":    ("take_screenshot", {},                                          "Taking a screenshot."),
        "EXIT":          ("exit",         {},                                             "Goodbye!"),
        "SHUTDOWN":      ("exit",         {},                                             "Goodbye!"),
        "NEXT_TRACK":    ("next_track",   {},                                             "Skipping to the next track."),
        "PREV_TRACK":    ("prev_track",   {},                                             "Going back to the previous track."),
        "PLAY_MEDIA":    ("play_media",   {},                                             "Resuming media playback."),
        "OPEN_FOLDER":   ("open_folder",  {"name": params.get("name", "downloads")},     "Opening folder."),
        "FIND_FILE":     ("find_file",    {"query": params.get("query", user_query)},    "Searching for your files."),
        "MEDIA_CONTROL": (params.get("action", "pause_media"), {},                       ""),
        "SET_VOLUME":    ("set_volume",   {"level": params.get("level", "50")},          ""),
        "WINDOW_MANAGEMENT": ("window_management", {"action": params.get("action", "")}, "Done."),
        "TAKE_PHOTO":    ("take_screenshot", {},                                          "Taking a photo."),
        "READ_SCREEN":   ("ask_about_screen", {"question": user_query},                   ""),
        "ASK_ABOUT_SCREEN": ("ask_about_screen", {"question": params.get("question", user_query)}, ""),
        "TYPE_TEXT":     ("type_text",    {"app": params.get("app", ""), "text": params.get("text", "")}, "On it! Writing that into your document now."),
        "OPEN_APP":      ("open_app",     {"name": app_name},                            app_msg),
        "SYSTEM_STATUS": ("system_status", {},                                            "Checking system status."),
        "SET_BRIGHTNESS":("set_brightness", {"level": params.get("level", "50")},        "Adjusting brightness."),
        "OPEN_SETTINGS": ("open_settings",  {"setting": params.get("setting", "wifi")},  "Opening settings."),
        "SET_TIMER":     ("set_timer",       params,                                       ""),
        "SET_REMINDER":  ("set_reminder",    params,                                       ""),
        "LIST_REMINDERS":("list_reminders",  {},                                           ""),
        "CANCEL_REMINDER":("cancel_reminder",{},                                           ""),
        "LOCK_PC":           ("lock_pc",           {}, "Locking your PC."),
        "SLEEP_PC":          ("sleep_pc",          {}, "Putting system to sleep."),
        "EMPTY_RECYCLE_BIN": ("empty_recycle_bin", {}, "Recycle bin emptied."),
        "RESTART_PC":        ("restart_pc",        {}, "Restarting your system in 30 seconds. Say cancel shutdown to abort."),
        "CANCEL_SHUTDOWN":   ("cancel_shutdown",   {}, "Shutdown schedule cancelled."),
    }


def _intent_to_agent_action(intent_data: dict, user_query: str) -> dict:
    """Converts regex-based intent dict to the agent action format. Fallback when LLM JSON fails."""
    intent = intent_data.get("intent", "GENERAL_CHAT")
    params = intent_data.get("params", {})

    mapping = _build_intent_map(params, user_query)
    if intent in mapping:
        tool, tool_params, speak_text = mapping[intent]
        return {"tool": tool, "params": tool_params, "speak": speak_text}

    # GENERAL_CHAT / date / time — speak field left empty; caller invokes get_ai_response() with live clock context
    return {"tool": "chat", "params": {}, "speak": ""}


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json_object(text: str) -> str | None:
    """
    Extracts the first complete JSON object from text using bracket-counting.
    More reliable than a non-greedy regex which stops at the FIRST closing brace.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start: i + 1]
    return None


# ---------------------------------------------------------------------------
# Core Agentic Dispatcher
# ---------------------------------------------------------------------------

def get_agent_action(user_query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Core agentic dispatcher: sends the user query + available tools to the local
    Qwen 2.5 3B Instruct model, which outputs a JSON array of action decisions.

    Returns a list of dicts: [{"tool": str, "params": dict, "speak": str}, ...]
    Always returns a list — callers should iterate over it.
    """
    def _normalise_action(action: dict) -> dict:
        """Ensure a single action dict has all required keys and clean values."""
        action.setdefault("params", {})
        if action.get("tool") in ("chat", "web_search"):
            action["speak"] = ""
        else:
            speak_val = action.get("speak", "")
            action["speak"] = sanitize_for_tts(speak_val) if speak_val else ""
        remember = action.get("remember", "")
        action["remember"] = remember.strip() if isinstance(remember, str) else ""
        return action

    def _parse_actions(raw: str) -> list[dict]:
        """Extract and parse a JSON array or single object from raw LLM output."""
        valid: list[dict] = []
        # Try to find a JSON array first
        arr_match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if arr_match:
            try:
                parsed = json.loads(arr_match.group())
                if isinstance(parsed, list):
                    valid = [_normalise_action(a) for a in parsed if isinstance(a, dict) and "tool" in a]
            except json.JSONDecodeError:
                pass

        # Fall back to a single JSON object
        if not valid:
            obj_str = _extract_json_object(raw)
            if obj_str:
                try:
                    action = json.loads(obj_str)
                    if isinstance(action, dict) and "tool" in action:
                        valid = [_normalise_action(action)]
                except json.JSONDecodeError:
                    pass

        if not valid:
            return [{"tool": "chat", "params": {}, "speak": ""}]

        # If all actions are chat, collapse to a single chat action
        if all(a.get("tool") == "chat" for a in valid):
            return [{"tool": "chat", "params": {}, "speak": ""}]

        # Deduplicate actions while preserving multi-action order
        deduped = []
        has_chat = False
        for a in valid:
            if a["tool"] == "chat":
                if not has_chat:
                    deduped.append(a)
                    has_chat = True
            elif not deduped or deduped[-1]["tool"] != a["tool"]:
                deduped.append(a)

        return deduped if deduped else [{"tool": "chat", "params": {}, "speak": ""}]

    # -----------------------------------------------------------------
    # Zero-latency shortcuts for explicit system lifecycle commands only
    # -----------------------------------------------------------------
    cleaned_lower = user_query.strip().lower()
    if cleaned_lower in ("exit", "quit", "goodbye", "bye", "shutdown", "close amigo"):
        return [{"tool": "exit", "params": {}, "speak": "Goodbye!"}]
    if cleaned_lower in ("lock pc", "lock my pc", "lock screen", "lock computer"):
        return [{"tool": "lock_pc", "params": {}, "speak": "Locking your PC."}]
    if cleaned_lower in ("sleep pc", "put pc to sleep", "sleep computer"):
        return [{"tool": "sleep_pc", "params": {}, "speak": "Putting system to sleep."}]
    if cleaned_lower in ("cancel shutdown", "cancel restart", "abort shutdown"):
        return [{"tool": "cancel_shutdown", "params": {}, "speak": "Shutdown schedule cancelled."}]

    # Zero-latency Timer & Stopwatch dispatch
    if "stopwatch" in cleaned_lower:
        return [{"tool": "stopwatch", "params": {"mode": "stopwatch", "label": "Stopwatch"}, "speak": "Starting stopwatch."}]
    if _RE_SET_TIMER.search(cleaned_lower):
        from reminder_timer import parse_relative_seconds
        dur_sec = parse_relative_seconds(cleaned_lower) or 300
        mins = dur_sec // 60
        secs = dur_sec % 60
        desc = f"{mins} minute{'s' if mins != 1 else ''}" if mins > 0 else f"{secs} seconds"
        return [{
            "tool": "set_timer",
            "params": {"duration": dur_sec, "label": "", "seconds": dur_sec},
            "speak": f"Setting a timer for {desc}."
        }]

    llm = init_local_llm()

    if llm is not None:
        try:
            # Inject active working state & structured user profile
            active_ctx = ""
            user_prof_ctx = ""
            try:
                from ai import get_active_context_prompt, get_user_profile_prompt
                active_ctx = get_active_context_prompt()
                user_prof_ctx = get_user_profile_prompt()
            except Exception:
                pass

            # Build conversation context (last 10 turns, up to 300 chars each)
            context_lines = ""
            if conversation_history:
                recent = conversation_history[-10:]
                context_lines = "\n".join(
                    f"User: {c.get('user', '')[:300]}\nAssistant: {c.get('assistant', '')[:300]}"
                    for c in recent
                    if isinstance(c, dict) and c.get("user")
                )

            # Inject current date/time so LLM can answer date/time questions
            now = datetime.datetime.now()
            date_ctx = (
                f"[Date/Time: {now.strftime('%A, %B %d, %Y')} "
                f"at {now.strftime('%I:%M %p')}]"
            )

            system_ctx = AGENT_SYSTEM_PROMPT + "\n" + date_ctx

            if active_ctx:
                system_ctx += f"\n{active_ctx}"

            if user_prof_ctx:
                system_ctx += f"\n{user_prof_ctx}"

            if context_lines:
                system_ctx += f"\n\n[Recent Conversation]:\n{context_lines}"

            clipboard_text = get_clipboard_text()
            if clipboard_text and len(clipboard_text.strip()) > 3:
                if clipboard_text.strip() != user_query.strip():
                    system_ctx += f"\n\n[Clipboard Reference: '{clipboard_text}']"

            user_instruction = f"{user_query}\nOutput ONLY a valid JSON array:"

            raw = ""
            if hasattr(llm, "create_chat_completion"):
                try:
                    res = llm.create_chat_completion(
                        messages=[
                            {"role": "system", "content": system_ctx},
                            {"role": "user", "content": user_instruction},
                        ],
                        max_tokens=768,
                        temperature=0.1,
                        stop=STOP_TOKENS,
                    )
                    raw = res["choices"][0]["message"]["content"].strip()
                except Exception as e_chat:
                    logger.debug(f"[Agent] Chat completion fallback note: {e_chat}")
                    raw = ""

            if not raw:
                full_prompt, stop_toks = format_chat_prompt(system_ctx, user_instruction)
                _old_stderr = sys.stderr
                sys.stderr = _devnull
                try:
                    res = llm.create_completion(
                        full_prompt,
                        max_tokens=768,
                        stop=stop_toks,
                        temperature=0.1,
                    )
                    raw = res["choices"][0]["text"].strip()
                finally:
                    sys.stderr = _old_stderr

            logger.debug(f"[Agent] Raw LLM output: {raw}")

            actions = _parse_actions(raw)
            if actions:
                logger.debug(f"[Agent] Decisions: {[a['tool'] for a in actions]}")
                return actions

        except Exception as e:
            logger.debug(f"[Agent] LLM dispatch failed: {e}")

    # Fallback to rule-based intent parsing
    fast_intent = parse_user_intent_with_llm(user_query)
    return [_normalise_action(_intent_to_agent_action(fast_intent, user_query))]


