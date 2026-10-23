"""
LLM-Based Agent for Amigo Voice Assistant.
Optimized for Spark-X2.6 4B with:
- Static system prompt prefix for instant LlamaRAMCache KV-cache reuse
- Full multi-turn conversation history preservation
- Single-pass RAG profile lookup per turn
"""

import contextvars
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
from pathlib import Path
from typing import Generator

try:
    import pyperclip
except ImportError:
    pyperclip = None

# Clean, single-pass project root resolution
_FILE_PATH = Path(__file__).resolve()
_PROJECT_ROOT = next(
    (p for p in _FILE_PATH.parents if (p / "amigo").is_dir() or (p / "pyproject.toml").exists()),
    _FILE_PATH.parents[1] if len(_FILE_PATH.parents) > 1 else _FILE_PATH.parent,
)
for _cand in (_PROJECT_ROOT, _PROJECT_ROOT / "amigo"):
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))


def _setup_cuda_dll_directory() -> None:
    """Ensure CUDA and cuBLAS runtime DLLs are accessible on Windows."""
    if sys.platform != "win32":
        return
    try:
        import site
        sp_list = [*(site.getsitepackages() or []), site.getusersitepackages()]
        cuda_dirs = [
            os.path.join(sp, "nvidia", sub, "bin")
            for sp in sp_list if isinstance(sp, str)
            for sub in ("cuda_runtime", "cublas", "cuda_nvrtc")
        ]
        for env_key in ("CUDA_PATH", "CUDA_HOME", "CUDA_PATH_V12_4", "CUDA_PATH_V12_5", "CUDA_PATH_V12_0", "CUDA_PATH_V11_8"):
            if cuda_home := os.environ.get(env_key):
                cuda_dirs.append(os.path.join(cuda_home, "bin"))

        path_env = os.environ.get("PATH", "")
        for d in cuda_dirs:
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
                if d not in path_env:
                    path_env = d + os.pathsep + path_env
        os.environ["PATH"] = path_env
    except Exception:
        pass


_setup_cuda_dll_directory()

import amigo.core.rag_engine as rag_engine
from amigo.core.rag_engine import get_recent_conversations
from amigo.utils.network_utils import is_internet_connected

logger = logging.getLogger("amigo.llm_agent")

_thread_local = threading.local()
_internet_status_cache = {"status": "Connected (Online)", "online": True, "timestamp": 0.0}
_INTERNET_CACHE_TTL = 30.0

_CLIPBOARD_KEYWORDS = ("clipboard", "copied", "what did i copy", "paste")
_RE_SPEAKER_PREFIX = re.compile(r"^(Amigo|Assistant|AI):\s*", flags=re.IGNORECASE)

_RE_PROBE_GUARD = re.compile(
    r"\b(what (?:tools?|functions?|capabilities) do you have|list your tools|how were you built|"
    r"what model are you|what is your prompt|output your json|tell me a secret)\b",
    re.IGNORECASE,
)

_last_screen_text = ""
_last_screen_time = 0.0

# Static System Prompts (Immutable for 100% llama.cpp KV-Cache Prefix Reuse)
_STATIC_VOICE_SYSTEM_PROMPT = (
    "You are Amigo, a friendly, intelligent, and capable personal AI voice assistant on the user's Windows PC.\n"
    "Core Instructions:\n"
    "- State the direct answer or solution immediately in the first sentence.\n"
    "- Keep spoken answers concise and natural (1 to 3 sentences) unless the user asks for detailed explanations, lists, or code.\n"
    "- Never use robotic filler like 'Certainly!', 'Sure thing!', 'As an AI', or 'According to search results'.\n"
    "- Never deflect, offer menus of options, or redirect with questions like 'Would you like general information, a comparison, or help finding reliable sources?'. Answer directly and helpfully with what you know.\n"
    "- Only reference 'my available documents' or 'in your files' if the user explicitly asked about their documents, files, or uploaded notes. For general topics (products, vehicles, science, concepts), answer from general knowledge or live web search without mentioning documents.\n"
    "- Do NOT use markdown formatting (no asterisks, bolding, headers, bullet points, or backticks) in spoken mode.\n"
    "- Resolve pronouns ('it', 'that', 'they', 'he', 'she', 'there') and follow-up questions naturally from the conversation history and active environment state.\n"
    "- Answer truthfully using provided context, personal memories, or internal knowledge without fabricating facts."
)


def _day_period(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _get_internet_status() -> tuple[str, bool]:
    now = time.time()
    if now - _internet_status_cache["timestamp"] > _INTERNET_CACHE_TTL:
        online = is_internet_connected()
        _internet_status_cache["status"] = "Connected (Online)" if online else "Disconnected (Offline)"
        _internet_status_cache["online"] = online
        _internet_status_cache["timestamp"] = now
    return _internet_status_cache["status"], _internet_status_cache["online"]


def initialize_agent() -> bool:
    return init_local_llm() is not None


def get_last_thought() -> str:
    return getattr(_thread_local, "last_thought", "")


def get_quick_feedback(action_desc: str) -> str:
    if not action_desc:
        return "Done."
    desc = str(action_desc).strip()
    return f"{desc[:1].upper()}{desc[1:]}." if not desc.endswith(".") else f"{desc[:1].upper()}{desc[1:]}"


def set_last_screen_text(text: str) -> None:
    global _last_screen_text, _last_screen_time
    if text and isinstance(text, str) and len(text.strip()) > 10:
        _last_screen_text = text.strip()[:1500]
        _last_screen_time = time.time()


def get_last_screen_text(consume: bool = False) -> str:
    global _last_screen_text
    if not _last_screen_text or (time.time() - _last_screen_time > 60):
        _last_screen_text = ""
        return ""
    text = _last_screen_text
    if consume:
        _last_screen_text = ""
    return text


def get_clipboard_text() -> str | None:
    if pyperclip:
        try:
            text = pyperclip.paste()
            if text and isinstance(text, str):
                return text[:1000].strip()
        except Exception:
            pass
    return None


_master_prompt_provider = None


def set_master_prompt_provider(fn) -> None:
    """Register provider for the unified immutable system prompt shared across routing and synthesis."""
    global _master_prompt_provider
    _master_prompt_provider = fn


def get_static_system_prompt() -> str:
    """Return the master immutable system prompt to guarantee 100% KV-cache prefix hits in llama.cpp."""
    if callable(_master_prompt_provider):
        try:
            return _master_prompt_provider()
        except Exception:
            pass
    try:
        from amigo.utils.tool_registry import get_static_master_prompt
        return get_static_master_prompt()
    except Exception:
        pass
    return _STATIC_VOICE_SYSTEM_PROMPT


def build_turn_runtime_context(
    query: str,
    web_context: str = "",
    doc_context: str = "",
    pending_confirmation: str = "",
) -> str:
    """Assemble dynamic turn metadata (time, active state, user profile facts, web/doc context) in one place."""
    ctx_blocks: list[str] = []

    now = datetime.datetime.now()
    net_status, online = _get_internet_status()
    env_line = f"[Current Time: {now.strftime('%A, %B %d, %Y %I:%M %p').lstrip('0')} ({_day_period(now.hour)}) | Network: {net_status}]"
    if not online:
        env_line += " (Note: PC is currently offline)"
    ctx_blocks.append(env_line)

    if pending_confirmation:
        ctx_blocks.append(pending_confirmation)

    if active_ctx := rag_engine.get_active_context_prompt():
        ctx_blocks.append(active_ctx)

    state = rag_engine.get_active_state(clean_expired=True) or {}
    if gen_info := state.get("generated_content"):
        if isinstance(gen_info, dict) and gen_info.get("content"):
            ctx_blocks.append(f"[Active Draft: {gen_info.get('type', 'content')} about '{gen_info.get('topic', '')}']")

    # Single cached lookup for user profile & facts
    if user_prof := rag_engine.get_user_profile_prompt(query, semantic_search=True):
        ctx_blocks.append(f"[User Profile & Memory]:\n{user_prof}")

    if screen := get_last_screen_text():
        ctx_blocks.append(f"[Screen OCR]: {screen[:300]}")

    if web_context:
        ctx_blocks.append(f"[Live Web Search Results]:\n{web_context}")
    if doc_context:
        ctx_blocks.append(f"[Retrieved Local Context]:\n{doc_context}")

    return "\n\n".join(ctx_blocks)


def build_unified_history_messages(
    conversation_history: list | None = None,
    max_turns: int = 3,
) -> list[dict]:
    """
    Format past conversation turns into identical user / assistant messages
    shared across both Pass 1 (Router) and Pass 2 (Synthesis) to guarantee 100% prefix KV cache hits.
    """
    turns = (
        [t for t in conversation_history[-max_turns:] if isinstance(t, dict)]
        if conversation_history
        else (rag_engine.get_recent_conversations(count=max_turns) or [])
    )
    low_val_tools = getattr(rag_engine, "LOW_VALUE_TOOLS", set())
    formatted = []
    for t in turns:
        tool = (t.get("tool") or "chat").strip().lower()
        if tool in low_val_tools or tool == "error":
            continue
        u = (t.get("user") or t.get("query") or "").strip()
        a = (t.get("assistant") or t.get("response") or "").strip()
        if u and a:
            clean_a, _ = _strip_reasoning(a)
            spoken = (clean_a.strip() or a)[:400]
            formatted.append({"role": "user", "content": u[:300]})
            formatted.append({"role": "assistant", "content": spoken or "Done."})
    return formatted


def _build_ai_messages(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
    conversation_history: list | None = None,
    is_voice: bool = True,
) -> list[dict]:
    """Assemble multi-turn chat messages sharing identical prefix with Pass 1 for 100% KV cache reuse."""
    messages: list[dict] = [{"role": "system", "content": get_static_system_prompt()}]

    if use_memory:
        history_count = 3 if is_voice else 5
        messages.extend(build_unified_history_messages(conversation_history=conversation_history, max_turns=history_count))

    runtime_ctx = build_turn_runtime_context(query, web_context=web_context, doc_context=doc_context)
    mode_tag = "[Mode: Voice | Output: Direct Spoken Answer]" if is_voice else "[Mode: Text | Output: Direct Formatted Answer]"
    if is_thinking_enabled():
        mode_tag += " [Reasoning: Enclose internal thinking strictly inside <think>...</think> tags]"
    user_content = f"{runtime_ctx}\n\nUser Question: {query}\n{mode_tag}" if runtime_ctx else f"{query}\n{mode_tag}"
    messages.append({"role": "user", "content": user_content})
    return messages


def get_ai_response(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
    is_voice: bool = True,
    conversation_history: list | None = None,
    max_tokens: int | None = None,
) -> str:
    """Query the local model with multi-turn memory, web, or document context."""
    _thread_local.last_thought = ""
    if not query or not query.strip():
        return "How can I help you today?"

    messages = _build_ai_messages(
        query,
        use_memory=use_memory,
        web_context=web_context,
        doc_context=doc_context,
        conversation_history=conversation_history,
        is_voice=is_voice,
    )
    thinking_active = is_thinking_enabled()
    limit = max_tokens or (1024 if thinking_active else (220 if is_voice else 512))
    sampling = get_sampling_params()

    response = query_local_llm(
        messages,
        system_prompt="",
        max_tokens=limit,
        temperature=sampling["temperature"],
        thinking=thinking_active,
        sanitize=False,
    )
    response = _RE_SPEAKER_PREFIX.sub("", response).strip()
    response, thought = _strip_reasoning(response, fallback_to_thought=True)
    _thread_local.last_thought = thought
    if is_voice:
        response = sanitize_for_tts(response) or response
    return response.strip() or "How can I help you today?"


def get_ai_response_stream(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
    interruption_event: threading.Event | None = None,
    is_voice: bool = True,
    conversation_history: list | None = None,
) -> Generator[str, None, None]:
    """Stream sentence chunks from the local model with full context."""
    if not query or not query.strip():
        yield "How can I help you today?"
        return

    messages = _build_ai_messages(
        query,
        use_memory=use_memory,
        web_context=web_context,
        doc_context=doc_context,
        conversation_history=conversation_history,
        is_voice=is_voice,
    )
    thinking_active = is_thinking_enabled()
    max_tokens = 1024 if thinking_active else (220 if is_voice else 512)
    sampling = get_sampling_params()
    token_gen = query_local_llm_stream(
        messages,
        system_prompt="",
        max_tokens=max_tokens,
        interruption_event=interruption_event,
        temperature=sampling["temperature"],
        thinking=thinking_active,
    )

    for sentence in stream_sentence_chunks(token_gen, interruption_event=interruption_event, thinking_enabled=thinking_active):
        clean = _RE_SPEAKER_PREFIX.sub("", sentence).strip()
        if clean:
            yield clean


# ── Memory & Profile Delegations ───────────────────────────────
load_memory = rag_engine.load_memory
save_memory = rag_engine.save_memory
get_active_state = rag_engine.get_active_state
update_active_state = rag_engine.update_active_state
extract_user_profile_updates = rag_engine.extract_user_profile_updates
get_user_profile_prompt = rag_engine.get_user_profile_prompt
get_active_context_prompt = rag_engine.get_active_context_prompt


def clear_conversations_memory(clear_profile: bool = False) -> None:
    rag_engine.clear_conversations()
    if clear_profile:
        rag_engine.save_profile(rag_engine._default_profile())


def add_to_memory(
    user_query: str,
    assistant_reply: str,
    tool: str = "chat",
    params: dict | None = None,
    clipboard_used: bool = False,
    remember: str = "",
    state_update: dict | None = None,
    url: str | None = None,
) -> None:
    rag_engine.add_conversation(
        user_msg=user_query,
        assistant_msg=assistant_reply,
        tool=tool,
        params=params,
        clipboard_used=clipboard_used,
        remember=remember,
        state_update=state_update,
        url=url,
    )


# ═══════════════════════════════════════════════════════════════
#  Low-Level Local LLM Inference Engine (Spark-X2.6 4B GGUF)
# ═══════════════════════════════════════════════════════════════

_PHYSICAL_CORES = None
_LLM_CONFIG = None
_CONFIG_LOCK = threading.Lock()

MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))
MODEL_NAME = "Spark-X2.6 4B"
MODEL_FILENAME = "Spark-X2.6-4B-Q4_K_M.gguf"
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)
MODEL_TARGETS = [
    ("abenzerps/Spark-X2.6-4B-Quantized", "Spark-X2.6-4B-Q4_K_M.gguf"),
]

STOP_TOKENS = [
    "<|im_start|>", "<|im_end|>", "<|endoftext|>", "<|end_of_text|>",
    "<|end_of_role|>", "\nUser:", "\nHuman:", "\nAssistant:",
]

AVAILABLE_MODELS = {
    "spark-x2.6-4b": {
        "key": "spark-x2.6-4b",
        "name": "Spark-X2.6 4B",
        "filename": "Spark-X2.6-4B-Q4_K_M.gguf",
        "path": os.path.join(MODEL_DIR, "Spark-X2.6-4B-Q4_K_M.gguf"),
        "targets": [("abenzerps/Spark-X2.6-4B-Quantized", "Spark-X2.6-4B-Q4_K_M.gguf")],
        "downloaded": os.path.exists(os.path.join(MODEL_DIR, "Spark-X2.6-4B-Q4_K_M.gguf")) and os.path.getsize(os.path.join(MODEL_DIR, "Spark-X2.6-4B-Q4_K_M.gguf")) > 500_000_000,
        "size_gb": 2.47,
    },
}

_active_model_key = "spark-x2.6-4b"
_local_llm_instance = None
_llm_lock = threading.Lock()
_inference_lock = threading.Lock()

_model_load_failed = False
_model_load_error = None
_model_loading = False
_model_load_failed_at = 0.0
_MODEL_RETRY_SECONDS = 60.0
_model_load_event = threading.Event()

_thinking_ctx: contextvars.ContextVar[bool | None] = contextvars.ContextVar("thinking_ctx", default=None)
_thinking_enabled: bool = False
_creativity_enabled: bool = True


def _get_physical_cores() -> int:
    global _PHYSICAL_CORES
    if _PHYSICAL_CORES is None:
        try:
            import psutil
            _PHYSICAL_CORES = psutil.cpu_count(logical=False) or multiprocessing.cpu_count()
        except Exception:
            _PHYSICAL_CORES = multiprocessing.cpu_count()
    return _PHYSICAL_CORES


def _get_llm_config() -> dict:
    global _LLM_CONFIG
    if _LLM_CONFIG is not None:
        return _LLM_CONFIG
    with _CONFIG_LOCK:
        if _LLM_CONFIG is not None:
            return _LLM_CONFIG
        cores = _get_physical_cores()
        total_cpus = multiprocessing.cpu_count()
        has_gpu = False
        try:
            import llama_cpp
            has_gpu = llama_cpp.llama_supports_gpu_offload()
        except Exception:
            pass

        # 8192 context with Q8_0 KV-cache offload (~360MB) fits comfortably within 4GB RTX 3050 VRAM alongside 2.45GB model weights
        default_ctx = "8192"
        _LLM_CONFIG = {
            "n_ctx": int(os.getenv("AMIGO_LLM_N_CTX", default_ctx)),
            "n_threads": int(os.getenv("AMIGO_LLM_N_THREADS", str(max(1, cores - 1)))),
            "n_threads_batch": int(os.getenv("AMIGO_LLM_N_THREADS_BATCH", str(cores))),
            "n_batch": int(os.getenv("AMIGO_LLM_N_BATCH", "1024")),
            "n_ubatch": int(os.getenv("AMIGO_LLM_N_UBATCH", "512")),
            "n_gpu_layers": int(os.getenv("AMIGO_LLM_N_GPU_LAYERS", "-1" if has_gpu else "0")),
            "use_mmap": os.getenv("AMIGO_LLM_USE_MMAP", "true").lower() == "true",
            "logits_all": False,
            "embedding": False,
            "offload_kqv": has_gpu,
            "flash_attn": has_gpu,
            "type_k": 8 if has_gpu else 0,
            "type_v": 8 if has_gpu else 0,
            "numa": False,
        }
    return _LLM_CONFIG


def _download_hf_file(targets: list[tuple[str, str]], target_path: str, min_size: int, label: str) -> str:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path) and os.path.getsize(target_path) >= min_size:
        return target_path

    for repo, fname in targets:
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(repo_id=repo, filename=fname, local_dir=MODEL_DIR)
            if os.path.exists(downloaded) and os.path.getsize(downloaded) >= min_size:
                if downloaded != target_path and not os.path.exists(target_path):
                    os.replace(downloaded, target_path)
                return target_path
        except Exception as e:
            logger.warning("Download attempt failed for %s/%s: %s", repo, fname, e)
    return target_path


def get_model_path() -> str:
    active_info = AVAILABLE_MODELS.get(_active_model_key, next(iter(AVAILABLE_MODELS.values())))
    return _download_hf_file(
        active_info.get("targets", MODEL_TARGETS),
        active_info["path"],
        500_000_000,
        active_info["name"],
    )


def is_vision_ready() -> bool:
    return False


def ensure_model_downloaded() -> str:
    return get_model_path()


def init_local_llm(force_reload: bool = False):
    global _local_llm_instance, _model_load_failed, _model_load_error, _model_loading, _model_load_failed_at

    if not force_reload:
        if _local_llm_instance is not None:
            return _local_llm_instance
        if _model_load_failed and (time.time() - _model_load_failed_at) < _MODEL_RETRY_SECONDS:
            return None

    with _llm_lock:
        if not force_reload:
            if _local_llm_instance is not None:
                return _local_llm_instance
            if _model_load_failed and (time.time() - _model_load_failed_at) < _MODEL_RETRY_SECONDS:
                return None

        _local_llm_instance = None
        _model_load_failed = False
        _model_load_error = None
        _model_loading = True
        _model_load_event.clear()

        try:
            model_file = get_model_path()
            if not os.path.exists(model_file) or os.path.getsize(model_file) < 500_000_000:
                _model_load_failed = True
                _model_load_failed_at = time.time()
                _model_load_error = f"Model file not found: {model_file}"
                return None

            from llama_cpp import Llama
            config = _get_llm_config()
            kwargs = {
                "model_path": model_file,
                "n_ctx": config["n_ctx"],
                "n_gpu_layers": config["n_gpu_layers"],
                "main_gpu": 0,
                "n_threads": config["n_threads"],
                "n_threads_batch": config["n_threads_batch"],
                "n_batch": config["n_batch"],
                "n_ubatch": config["n_ubatch"],
                "flash_attn": config["flash_attn"],
                "use_mmap": config["use_mmap"],
                "logits_all": False,
                "embedding": False,
                "offload_kqv": config["offload_kqv"],
                "type_k": config.get("type_k", 8 if config["offload_kqv"] else 0),
                "type_v": config.get("type_v", 8 if config["offload_kqv"] else 0),
                "numa": False,
                "verbose": False,
            }
            try:
                _local_llm_instance = Llama(**kwargs)
                logger.info("[Local AI Engine] Successfully initialized %s on GPU (RTX 3050, n_gpu_layers=%s, n_ctx=%s, flash_attn=%s)", MODEL_NAME, kwargs["n_gpu_layers"], kwargs["n_ctx"], kwargs["flash_attn"])
                print(f"[Local AI Engine] Spark-X2.6 is running on GPU: NVIDIA GeForce RTX 3050 (n_gpu_layers={kwargs['n_gpu_layers']}, n_ctx={kwargs['n_ctx']}, flash_attn={kwargs['flash_attn']})", flush=True)
            except Exception as gpu_err:
                logger.warning("[Local AI Engine] GPU load attempt failed (%s), falling back to CPU", gpu_err)
                kwargs["n_gpu_layers"] = 0
                kwargs["offload_kqv"] = False
                kwargs["flash_attn"] = False
                kwargs["type_k"] = 0
                kwargs["type_v"] = 0
                _local_llm_instance = Llama(**kwargs)

            _setup_chat_formatter(_local_llm_instance)
            return _local_llm_instance
        except Exception as e:
            _model_load_failed = True
            _model_load_failed_at = time.time()
            _model_load_error = str(e)
            logger.error("[Local AI Engine] Load error: %s", e)
            return None
        finally:
            _model_loading = False
            _model_load_event.set()


def set_thinking_enabled(enabled: bool) -> None:
    global _thinking_enabled
    _thinking_enabled = bool(enabled)


def is_thinking_enabled() -> bool:
    return _thinking_enabled


def get_current_thinking_enabled() -> bool:
    ctx_val = _thinking_ctx.get()
    return ctx_val if ctx_val is not None else _thinking_enabled


def set_creativity_enabled(enabled: bool) -> None:
    global _creativity_enabled
    _creativity_enabled = bool(enabled)


def is_creativity_enabled() -> bool:
    return _creativity_enabled


def get_sampling_params() -> dict:
    return {"temperature": 0.3 if _creativity_enabled else 0.15, "top_p": 0.95, "min_p": 0.05}


def _setup_chat_formatter(llm) -> None:
    try:
        from llama_cpp import llama_chat_format
        handler = llm.chat_handler or getattr(llm, "_chat_handlers", {}).get(llm.chat_format)
        if handler and getattr(handler, "__closure__", None):
            formatters = [c.cell_contents for c in handler.__closure__ if hasattr(c.cell_contents, "template")]
            if formatters:
                base_formatter = formatters[0]

                class ThinkingJinja2ChatFormatter:
                    def __init__(self, base):
                        self.base = base

                    def __call__(self, *args, **kwargs):
                        thinking = kwargs.get("enable_thinking")
                        if thinking is None:
                            thinking = get_current_thinking_enabled()
                        kwargs["enable_thinking"] = bool(thinking)
                        chat_kw = kwargs.setdefault("chat_template_kwargs", {})
                        if isinstance(chat_kw, dict):
                            chat_kw["enable_thinking"] = bool(thinking)
                        return self.base(*args, **kwargs)

                new_handler = llama_chat_format.chat_formatter_to_chat_completion_handler(
                    ThinkingJinja2ChatFormatter(base_formatter)
                )
                llm.chat_handler = new_handler
    except Exception:
        pass


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_RE_THINK_TAGS = re.compile(r"<(?:think|thought)>([\s\S]*?)</(?:think|thought)>", re.I)
_RE_LABELED_THOUGHT = re.compile(
    r"(?i)(?:^|\n)\s*(?:\[?(?:thought|thinking(?:\s+process)?|reasoning|scratchpad)\]?[:\s]+)([\s\S]*?)(?=(?:\n\s*(?:(?:final\s+)?answer|conclusion|response)[:\s]+)|\Z)"
)
_RE_META_INTRO = re.compile(
    r"(?i)^(?:let(?:\'s| me)\s+(?:work through|think about|break this down|analyze|figure out|calculate|solve)[^.\n]*[.:\n]\s*)+"
)


def _strip_reasoning(text: str, fallback_to_thought: bool = False) -> tuple[str, str]:
    if not text:
        return "", ""
    thoughts = [m.strip() for m in _RE_THINK_TAGS.findall(text)]
    clean = _RE_THINK_TAGS.sub("", text)

    k = clean.lower().find(_THINK_OPEN)
    if k != -1:
        thoughts.append(clean[k + len(_THINK_OPEN):].strip())
        clean = clean[:k]
    j = clean.lower().rfind(_THINK_CLOSE)
    if j != -1:
        thoughts.append(clean[:j].strip())
        clean = clean[j + len(_THINK_CLOSE):]

    for m in _RE_LABELED_THOUGHT.finditer(clean):
        if th := m.group(1).strip():
            thoughts.append(th)
    clean = _RE_LABELED_THOUGHT.sub("", clean)
    clean = re.sub(r"(?i)^\s*(?:(?:final\s+)?answer|conclusion|response)[:\s]+", "", clean).strip()
    clean = _RE_META_INTRO.sub("", clean).strip() or clean.strip()

    if not clean and fallback_to_thought and thoughts:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", thoughts[-1]) if len(s.strip()) > 5]
        clean = sentences[-1] if sentences else thoughts[-1]
    return clean, "\n".join(t for t in thoughts if t)


_RE_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```")
_RE_UNCLOSED_FENCE = re.compile(r"```[\s\S]*$")
_RE_MD_MARKS = re.compile(r"[*~`]")
_RE_MD_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_RE_MD_BULLET = re.compile(r"(?m)^\s*[-\u2022]\s+")
_RE_INTRAWORD_UNDERSCORE = re.compile(r"(?<=\w)_(?=\w)")
_RE_MD_LINKS = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_BRACKETS = re.compile(r"[{}\[\]\\<>]")
_RE_WHITESPACE = re.compile(r"\s+")
_TTS_ALLOWED_SYMBOLS = frozenset(" ,.!?:;-'\"+=\u00b0\u00d7\u00f7$\u20ac\u00a3\u00a5%@#&*()[]{}<>|\\/~`^_")
_TTS_ALLOWED_CATS = frozenset("LNPZM")


def _speak_currency(text: str) -> str:
    for symbol, word in (("\u20b9", "rupees"), ("$", "dollars"), ("\u20ac", "euros"), ("\u00a3", "pounds")):
        text = re.sub(re.escape(symbol) + r"(\d+(?:[.,]\d+)?)", rf"\1 {word}", text)
        text = text.replace(symbol, f" {word} ")
    return text


def _clean_markdown(text: str) -> str:
    text, _ = _strip_reasoning(text)
    text = _RE_CODE_BLOCK.sub(" ", text)
    text = _RE_UNCLOSED_FENCE.sub(" ", text)
    text = _RE_MD_LINKS.sub(r"\1", text)
    text = _RE_MD_HEADING.sub("", text)
    text = _RE_MD_BULLET.sub("", text)
    text = _RE_INTRAWORD_UNDERSCORE.sub(" ", text)
    text = _RE_MD_MARKS.sub("", text)
    text = _RE_BRACKETS.sub(" ", text)
    text = _speak_currency(text)
    return _RE_WHITESPACE.sub(" ", text).strip()


def _filter_tts_chars(text: str) -> str:
    return "".join(
        ch for ch in text
        if ch in _TTS_ALLOWED_SYMBOLS or unicodedata.category(ch)[0] in _TTS_ALLOWED_CATS or unicodedata.category(ch) in ("Sm", "Sc")
    ).strip()


def strip_markdown_for_tts(text: str) -> str:
    return _clean_markdown(text) if text else ""


def clean_tts_text(text: str) -> str:
    return _filter_tts_chars(text) if text else ""


def sanitize_for_tts(text: str) -> str:
    return _filter_tts_chars(_clean_markdown(text)) if text else ""


def _prepare_chat_messages(system_prompt: str, prompt: str | list[dict]) -> list[dict]:
    if isinstance(prompt, list):
        if prompt and prompt[0].get("role") == "system":
            return prompt
        return ([{"role": "system", "content": system_prompt}] if system_prompt else []) + prompt
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": str(prompt)}]


def query_local_llm(
    prompt: str | list[dict],
    system_prompt: str = _STATIC_VOICE_SYSTEM_PROMPT,
    max_tokens: int = 512,
    temperature: float | None = None,
    thinking: bool | None = None,
    sanitize: bool = True,
    response_format: dict | None = None,
    stop: list[str] | None = None,
    raise_on_error: bool = False,
    repeat_penalty: float | None = None,
    min_p: float | None = None,
) -> str:
    llm = init_local_llm()
    if not llm:
        if raise_on_error:
            raise RuntimeError(_model_load_error or "language model unavailable")
        return "I'm having trouble initializing the AI engine."

    token = _thinking_ctx.set(thinking) if thinking is not None else None
    try:
        sampling = get_sampling_params()
        is_thinking = bool(thinking if thinking is not None else get_current_thinking_enabled())
        kwargs = {
            "messages": _prepare_chat_messages(system_prompt, prompt),
            "max_tokens": max_tokens,
            "temperature": sampling["temperature"] if temperature is None else temperature,
            "top_p": sampling["top_p"],
            "min_p": sampling.get("min_p", 0.05) if min_p is None else min_p,
            "repeat_penalty": 1.05 if repeat_penalty is None else repeat_penalty,
            "stop": STOP_TOKENS if stop is None else stop,
        }
        if response_format:
            kwargs["response_format"] = response_format
        with _inference_lock:
            try:
                res = llm.create_chat_completion(**kwargs)
            except Exception as e:
                if "exceed context window" in str(e).lower() and kwargs.get("max_tokens", 0) > 128:
                    logger.warning("[LLM Query] Context overflow detected, retrying with compressed max_tokens...")
                    kwargs["max_tokens"] = 128
                    res = llm.create_chat_completion(**kwargs)
                else:
                    raise
        output = (res["choices"][0]["message"]["content"] or "").strip()
        return (sanitize_for_tts(output) or output) if sanitize else output
    except Exception as e:
        logger.error("[LLM Query Error]: %s", e)
        if raise_on_error:
            raise
        return "I'm having trouble processing that request."
    finally:
        if token is not None:
            try:
                _thinking_ctx.reset(token)
            except ValueError:
                pass


def query_local_llm_stream(
    prompt: str | list[dict],
    system_prompt: str = _STATIC_VOICE_SYSTEM_PROMPT,
    max_tokens: int = 512,
    interruption_event: threading.Event | None = None,
    temperature: float | None = None,
    thinking: bool | None = None,
) -> Generator[str, None, None]:
    llm = init_local_llm()
    if not llm:
        yield "I'm having trouble initializing the AI engine."
        return

    token = _thinking_ctx.set(thinking) if thinking is not None else None
    try:
        sampling = get_sampling_params()
        temp = sampling["temperature"] if temperature is None else temperature
        is_thinking = bool(thinking if thinking is not None else get_current_thinking_enabled())
        with _inference_lock:
            stream_res = None
            try:
                try:
                    stream_res = llm.create_chat_completion(
                        messages=_prepare_chat_messages(system_prompt, prompt),
                        max_tokens=max_tokens,
                        temperature=temp,
                        top_p=sampling["top_p"],
                        repeat_penalty=1.05,
                        stop=STOP_TOKENS,
                        stream=True,
                    )
                except Exception as e:
                    if "exceed context window" in str(e).lower() and max_tokens > 128:
                        logger.warning("[LLM Stream] Context overflow detected, retrying with compressed max_tokens...")
                        stream_res = llm.create_chat_completion(
                            messages=_prepare_chat_messages(system_prompt, prompt),
                            max_tokens=128,
                            temperature=temp,
                            top_p=sampling["top_p"],
                            repeat_penalty=1.05,
                            stop=STOP_TOKENS,
                            stream=True,
                        )
                    else:
                        raise
                for chunk in stream_res:
                    if interruption_event and interruption_event.is_set():
                        break
                    if delta := chunk["choices"][0]["delta"].get("content"):
                        yield delta
            except Exception as e:
                logger.error("[LLM Stream Error]: %s", e)
                yield "I'm having trouble processing that request."
            finally:
                if callable(getattr(stream_res, "close", None)):
                    try:
                        stream_res.close()
                    except Exception:
                        pass
    finally:
        if token is not None:
            try:
                _thinking_ctx.reset(token)
            except ValueError:
                pass


_RE_SENTENCE_SPLIT_CHUNKS = re.compile(r"(?<=[.!?])\s+|\n+")
_RE_TITLE_ABBREV = re.compile(
    r"\b(mr|mrs|ms|dr|vs|eg|ie|etc|prof|sr|jr|st|ave|blvd|rd|apt|vol|fig|eq|cf|ft|hr|lb|oz|pt|qt|yd|i\.e|e\.g)\.$",
    re.IGNORECASE,
)


def _filter_think_stream(tokens, thinking_enabled: bool = False):
    buf = ""
    in_think = thinking_enabled
    for tok in tokens:
        buf += tok
        out = []
        while True:
            if in_think:
                i = buf.find(_THINK_CLOSE)
                if i == -1:
                    buf = buf[-(len(_THINK_CLOSE) - 1):]
                    break
                buf = buf[i + len(_THINK_CLOSE):]
                in_think = False
            else:
                i = buf.find(_THINK_OPEN)
                if i == -1:
                    keep = next((k for k in range(min(len(_THINK_OPEN) - 1, len(buf)), 0, -1) if _THINK_OPEN.startswith(buf[-k:])), 0)
                    out.append(buf[:-keep] if keep else buf)
                    buf = buf[-keep:] if keep else ""
                    break
                out.append(buf[:i])
                buf = buf[i + len(_THINK_OPEN):]
                in_think = True
        if text := "".join(out):
            yield text
    if buf and not in_think:
        yield buf


def stream_sentence_chunks(
    token_generator,
    interruption_event: threading.Event | None = None,
    thinking_enabled: bool = False,
) -> Generator[str, None, None]:
    buffer = ""
    pending = ""
    in_code_block = False

    for token in _filter_think_stream(token_generator, thinking_enabled=thinking_enabled):
        if interruption_event and interruption_event.is_set():
            if hasattr(token_generator, "close"):
                token_generator.close()
            return
        token = token.replace("<|endoftext|>", "")
        buffer += token

        if "```" in token and token.count("```") % 2 == 1:
            in_code_block = not in_code_block

        if in_code_block:
            if "```" in buffer:
                parts = buffer.split("```", 1)
                if len(parts) > 1:
                    in_code_block = False
                    buffer = parts[1]
                else:
                    buffer = ""
            else:
                buffer = ""
            continue

        splits = _RE_SENTENCE_SPLIT_CHUNKS.split(buffer)
        if len(splits) > 1:
            for s in splits[:-1]:
                cand = s.strip()
                if not cand:
                    continue
                if pending:
                    cand = f"{pending} {cand}".strip()
                    pending = ""
                if _RE_TITLE_ABBREV.search(cand):
                    pending = cand
                    continue
                if clean := sanitize_for_tts(cand):
                    yield clean
            buffer = splits[-1]

    if not (interruption_event and interruption_event.is_set()) and not in_code_block:
        tail = buffer.strip()
        if tail or pending:
            if clean := sanitize_for_tts(f"{pending} {tail}".strip() if pending else tail):
                yield clean


def query_local_vision(*args, **kwargs) -> str | None:
    return None


def get_available_models() -> dict:
    for m in AVAILABLE_MODELS.values():
        m["downloaded"] = os.path.exists(m["path"]) and os.path.getsize(m["path"]) > 500_000_000
    return AVAILABLE_MODELS


def set_active_model(model_key: str = "spark-x2.6-4b") -> bool:
    global _active_model_key, _local_llm_instance, _LLM_CONFIG
    if model_key not in AVAILABLE_MODELS:
        return False
    with _inference_lock, _llm_lock, _CONFIG_LOCK:
        _active_model_key = model_key
        _local_llm_instance = None
        _LLM_CONFIG = None
    return init_local_llm(force_reload=True) is not None


def get_active_model_info() -> dict:
    m_info = AVAILABLE_MODELS.get(_active_model_key, next(iter(AVAILABLE_MODELS.values())))
    m_path = m_info["path"]
    has_gpu = False
    try:
        import llama_cpp
        has_gpu = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:
        pass
    return {
        "key": _active_model_key,
        "name": m_info["name"],
        "filename": m_info["filename"],
        "path": m_path,
        "downloaded": os.path.exists(m_path) and os.path.getsize(m_path) > 500_000_000,
        "size_gb": m_info.get("size_gb", 2.60),
        "vision_ready": False,
        "accelerator": "NVIDIA GeForce RTX 3050 (CUDA)" if has_gpu else "CPU",
        "gpu_offload": has_gpu,
        "flash_attn": has_gpu,
    }


# Forwarders to tool_registry for backward compatibility
def parse_user_intent_fast(query: str):
    from amigo.utils.tool_registry import parse_user_intent_fast as _fn
    return _fn(query)

def get_agent_actions(query: str, conversation_history: list | None = None):
    from amigo.utils.tool_registry import get_agent_actions as _fn
    return _fn(query, conversation_history)

def get_agent_actions_with_response(query: str, conversation_history: list | None = None):
    from amigo.utils.tool_registry import get_agent_actions_with_response as _fn
    return _fn(query, conversation_history)

def get_agent_action(query: str, conversation_history: list | None = None):
    from amigo.utils.tool_registry import get_agent_action as _fn
    return _fn(query, conversation_history)

def get_last_played_song(conversation_history: list | None = None):
    from amigo.utils.tool_registry import get_last_played_song as _fn
    return _fn(conversation_history)

def format_clarification_prompt(tool_a: str, tool_b: str, query: str) -> str:
    from amigo.utils.tool_registry import format_clarification_prompt as _fn
    return _fn(tool_a, tool_b, query)
