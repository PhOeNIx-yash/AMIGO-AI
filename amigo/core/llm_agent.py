"""
LLM-Based Agent for Amigo Voice Assistant.
Clean, streamlined inference engine for LFM 3 1.7B:
- Direct GGUF inference via llama-cpp-python on RTX 3050 GPU (with CPU fallback)
- Fast KV-cache reuse with unified system prompt prefix
- Multi-turn conversation context and RAG profile integration
- Clean, non-destructive streaming sentence synthesis for TTS
"""

import datetime
import json
import logging
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import re
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Generator, cast

if TYPE_CHECKING:
    from llama_cpp.llama_types import ChatCompletionRequestMessage, CreateChatCompletionStreamResponse

try:
    import pyperclip
except ImportError:
    pyperclip = None

# Single-pass project root resolution
_FILE_PATH = Path(__file__).resolve()
_PROJECT_ROOT = next(
    (p for p in _FILE_PATH.parents if (p / "amigo").is_dir() or (p / "pyproject.toml").exists()),
    _FILE_PATH.parents[1] if len(_FILE_PATH.parents) > 1 else _FILE_PATH.parent,
)
for _cand in (_PROJECT_ROOT, _PROJECT_ROOT / "amigo"):
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))


_cuda_dirs_setup: bool = False


def _setup_cuda_dll_directory() -> None:
    """Ensure CUDA and cuBLAS runtime DLLs as well as llama_cpp backends are accessible on Windows."""
    global _cuda_dirs_setup
    if _cuda_dirs_setup or sys.platform != "win32":
        return
    _cuda_dirs_setup = True
    try:
        import site
        sp_list = [*(site.getsitepackages() or []), site.getusersitepackages(), sys.prefix, os.path.join(sys.prefix, "Lib", "site-packages")]
        cuda_dirs = [
            os.path.join(sp, "nvidia", sub, "bin")
            for sp in sp_list if isinstance(sp, str)
            for sub in ("cuda_runtime", "cublas", "cuda_nvrtc")
        ]
        for env_key in ("CUDA_PATH", "CUDA_HOME", "CUDA_PATH_V12_4", "CUDA_PATH_V12_5", "CUDA_PATH_V12_0", "CUDA_PATH_V11_8"):
            if cuda_home := os.environ.get(env_key):
                cuda_dirs.append(os.path.join(cuda_home, "bin"))

        # Prioritize bundled modern llama.cpp binary directory (supports lfm3 architecture)
        bundled_llama = os.path.join(str(_PROJECT_ROOT), "amigo", "lib", "llama")
        if not os.path.isdir(bundled_llama):
            bundled_llama = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "llama")

        if os.path.isdir(bundled_llama):
            os.environ["LLAMA_CPP_LIB_PATH"] = bundled_llama
            cuda_dirs.insert(0, bundled_llama)
        else:
            try:
                import llama_cpp
                lib_dir = os.path.join(os.path.dirname(llama_cpp.__file__), "lib")
                if os.path.isdir(lib_dir):
                    cuda_dirs.append(lib_dir)
            except Exception:
                pass

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

        # Pre-load ggml backend plugins so llama.cpp doesn't report 'no backends are loaded'
        try:
            active_lib = os.environ.get("LLAMA_CPP_LIB_PATH")
            if not active_lib:
                import llama_cpp
                active_lib = os.path.join(os.path.dirname(llama_cpp.__file__), "lib")
            base_dll = os.path.join(active_lib, "ggml-base.dll")
            if os.path.exists(base_dll):
                try:
                    import ctypes
                    ctypes.CDLL(base_dll)
                except Exception:
                    pass
            ggml_dll = os.path.join(active_lib, "ggml.dll")
            if os.path.exists(ggml_dll):
                import ctypes
                ggml = ctypes.CDLL(ggml_dll)
                if hasattr(ggml, "ggml_backend_load_all_from_path"):
                    ggml.ggml_backend_load_all_from_path(active_lib.encode("utf-8"))
                elif hasattr(ggml, "ggml_backend_load_all"):
                    ggml.ggml_backend_load_all()
        except Exception:
            pass
    except Exception:
        pass


_setup_cuda_dll_directory()

import amigo.core.rag_engine as rag_engine
from amigo.utils.network_utils import is_internet_connected

logger = logging.getLogger("amigo.llm_agent")

_thread_local = threading.local()
_inference_lock = threading.Lock()
_llm_lock = threading.Lock()
_CONFIG_LOCK = threading.Lock()

_internet_status_cache = {"status": "Connected (Online)", "online": True, "timestamp": 0.0}
_INTERNET_CACHE_TTL = 30.0

_CLIPBOARD_KEYWORDS = ("clipboard", "copied", "what did i copy", "paste")
_RE_SPEAKER_PREFIX = re.compile(r"^(Amigo|Assistant|AI):\s*", flags=re.IGNORECASE)
_RE_PROBE_GUARD = re.compile(
    r"\b(what (?:tools?|functions?|capabilities) do you have|list your tools|how were you built|"
    r"what model are you|what is your prompt|output your json|tell me a secret)\b",
    re.IGNORECASE,
)
_RE_THINK = re.compile(r"<think>([\s\S]*?)(?:</think>|$)", re.IGNORECASE)

_last_screen_text = ""
_last_screen_time = 0.0

_STATIC_VOICE_SYSTEM_PROMPT = (
    "You are Amigo, a friendly, intelligent, and capable personal AI voice assistant on the user's Windows PC.\n"
    "Core Instructions:\n"
    "- State the direct answer or solution immediately in the first sentence.\n"
    "- Keep responses conversational and concise (1-3 spoken sentences) unless the user specifically asks for detailed explanations, lists, or steps.\n"
    "- Answer ONLY the user's current question. Never bring up past topics, previous commands, active links, or background media unless the user specifically asks about them.\n"
    "- Speak naturally and confidently. Never use robotic filler phrases like 'Certainly!', 'Sure thing!', 'As an AI', or 'According to my knowledge'.\n"
    "- Never deflect with multi-choice question menus. Answer directly with what you know.\n"
    "- Only reference 'available documents' or 'in your files' if the user specifically asked about their personal files.\n"
    "- For riddles, physics puzzles, and logical word problems, reason carefully through physical space, geometry, and real-world mechanics before giving the direct answer.\n"
    "- Resolve pronouns ('it', 'that', 'they', 'he', 'she', 'there') and follow-up questions naturally from conversation history and active environment state."
)

_master_prompt_provider = None


def set_master_prompt_provider(fn) -> None:
    global _master_prompt_provider
    _master_prompt_provider = fn


def get_static_system_prompt() -> str:
    """Conversational assistant system prompt for natural speech and text responses."""
    return _STATIC_VOICE_SYSTEM_PROMPT


def _get_internet_status() -> tuple[str, bool]:
    now = time.time()
    if now - _internet_status_cache["timestamp"] > _INTERNET_CACHE_TTL:
        online = is_internet_connected()
        _internet_status_cache["status"] = "Connected (Online)" if online else "Disconnected (Offline)"
        _internet_status_cache["online"] = online
        _internet_status_cache["timestamp"] = now
    return _internet_status_cache["status"], _internet_status_cache["online"]


def _day_period(hour: int) -> str:
    if 5 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 21:
        return "Evening"
    return "Night"


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


def build_turn_runtime_context(
    query: str,
    web_context: str = "",
    doc_context: str = "",
    pending_confirmation: str = "",
) -> str:
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

    state = rag_engine.get_active_state(clean_expired=False) or {}
    if gen_info := state.get("generated_content"):
        if isinstance(gen_info, dict) and gen_info.get("content"):
            ctx_blocks.append(f"[Active Draft: {gen_info.get('type', 'content')} about '{gen_info.get('topic', '')}']")

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
    history = conversation_history or rag_engine.get_recent_conversations(count=max_turns)
    formatted: list[dict] = []
    for turn in history[-max_turns:]:
        u = str(turn.get("user") or turn.get("user_query") or turn.get("query") or "").strip()
        a = str(turn.get("assistant") or turn.get("assistant_reply") or turn.get("response") or "").strip()
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
    messages: list[dict] = [{"role": "system", "content": get_static_system_prompt()}]
    if use_memory:
        history_count = 3 if is_voice else 5
        messages.extend(build_unified_history_messages(conversation_history=conversation_history, max_turns=history_count))

    runtime_ctx = build_turn_runtime_context(query, web_context=web_context, doc_context=doc_context)
    if doc_context:
        mode_tag = "[Mode: Document Analysis | Output: Structured, Well-Organized Markdown with Bullets, Clear Sections, and Exact Figures/Dates]"
    else:
        mode_tag = "[Mode: Voice | Output: Direct Spoken Answer]" if is_voice else "[Mode: Text | Output: Direct Formatted Answer]"
    user_content = f"{runtime_ctx}\n\nUser Question: {query}\n{mode_tag}" if runtime_ctx else f"{query}\n{mode_tag}"
    messages.append({"role": "user", "content": user_content})
    return messages


# ── Model Configuration & Lifecycle ────────────────────────────

MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))
MODEL_NAME = "LFM 3 1.7B"
MODEL_FILENAME = "LFM3-1.7B-Q4_K_M.gguf"
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)
MODEL_TARGETS = [
    ("LiquidAI/LFM3-1.7B-GGUF", "LFM3-1.7B-Q4_K_M.gguf"),
    ("bartowski/LFM-3-1.7B-GGUF", "LFM-3-1.7B-Q4_K_M.gguf"),
]

STOP_TOKENS = ["<eos>", "<turn|>", "<|turn|>", "<|endoftext|>", "<|im_end|>", "<|im_start|>", "<|eot_id|>"]

AVAILABLE_MODELS = {
    "lfm-3-1.7b": {
        "key": "lfm-3-1.7b",
        "name": "LFM 3 1.7B",
        "repo": "LiquidAI/LFM3-1.7B-GGUF",
        "filename": "LFM3-1.7B-Q4_K_M.gguf",
        "path": os.path.join(MODEL_DIR, "LFM3-1.7B-Q4_K_M.gguf"),
        "targets": [
            ("LiquidAI/LFM3-1.7B-GGUF", "LFM3-1.7B-Q4_K_M.gguf"),
            ("bartowski/LFM-3-1.7B-GGUF", "LFM-3-1.7B-Q4_K_M.gguf"),
        ],
        "downloaded": os.path.exists(os.path.join(MODEL_DIR, "LFM3-1.7B-Q4_K_M.gguf")) and os.path.getsize(os.path.join(MODEL_DIR, "LFM3-1.7B-Q4_K_M.gguf")) > 500_000_000,
        "size_gb": 1.10,
    },
}

_active_model_key = "lfm-3-1.7b"
_local_llm_instance = None
_model_load_failed = False
_model_load_error = None
_model_loading = False
_model_load_failed_at = 0.0
_MODEL_RETRY_SECONDS = 60.0
_PHYSICAL_CORES = None
_LLM_CONFIG = None

_thinking_enabled: bool = False
_creativity_enabled: bool = True


def set_thinking_enabled(enabled: bool) -> None:
    global _thinking_enabled
    _thinking_enabled = bool(enabled)


def is_thinking_enabled() -> bool:
    return _thinking_enabled


def get_current_thinking_enabled() -> bool:
    return _thinking_enabled


def set_creativity_enabled(enabled: bool) -> None:
    global _creativity_enabled
    _creativity_enabled = bool(enabled)


def is_creativity_enabled() -> bool:
    return _creativity_enabled


def get_sampling_params() -> dict:
    return {"temperature": 0.3 if _creativity_enabled else 0.15, "top_p": 0.95, "min_p": 0.05}


def _get_physical_cores() -> int:
    global _PHYSICAL_CORES
    if _PHYSICAL_CORES is None:
        try:
            import psutil
            _PHYSICAL_CORES = psutil.cpu_count(logical=False) or os.cpu_count() or 4
        except Exception:
            _PHYSICAL_CORES = os.cpu_count() or 4
    return _PHYSICAL_CORES


def _get_llm_config() -> dict:
    global _LLM_CONFIG
    if _LLM_CONFIG is not None:
        return _LLM_CONFIG
    with _CONFIG_LOCK:
        if _LLM_CONFIG is not None:
            return _LLM_CONFIG
        has_gpu = False
        try:
            import llama_cpp
            has_gpu = bool(llama_cpp.llama_supports_gpu_offload())
        except Exception:
            pass
        cores = _get_physical_cores()
        _LLM_CONFIG = {
            "n_ctx": int(os.getenv("AMIGO_LLM_N_CTX", "8192")),
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
    target_path = active_info["path"]
    if os.path.exists(target_path) and os.path.getsize(target_path) >= 500_000_000:
        return target_path

    # Try downloading the active model
    downloaded = _download_hf_file(
        active_info.get("targets", MODEL_TARGETS),
        target_path,
        500_000_000,
        active_info["name"],
    )
    if os.path.exists(downloaded) and os.path.getsize(downloaded) >= 500_000_000:
        return downloaded

    # Fallback to any already downloaded model if available on disk
    for m in AVAILABLE_MODELS.values():
        if os.path.exists(m["path"]) and os.path.getsize(m["path"]) >= 500_000_000:
            logger.info("[Local AI Engine] Falling back to available model on disk: %s", m["name"])
            return m["path"]

    return target_path


def is_vision_ready() -> bool:
    return False


def ensure_model_downloaded() -> str:
    path = get_model_path()
    if os.path.exists(path) and os.path.getsize(path) > 500_000_000:
        return path
    m_info = AVAILABLE_MODELS.get(_active_model_key, {})
    repo = m_info.get("repo", "LiquidAI/LFM3-1.7B-GGUF")
    filename = m_info.get("filename", MODEL_FILENAME)
    logger.info("Downloading %s (%s) to %s...", m_info.get("name", "Model"), filename, path)
    os.makedirs(MODEL_DIR, exist_ok=True)
    try:
        import subprocess
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        subprocess.run(
            ["curl.exe", "-4", "-L", "-C", "-", "--retry", "5", "--retry-delay", "2", "-o", path, url],
            check=True
        )
        return path
    except Exception as e:
        logger.warning("curl download failed (%s), trying hf_hub_download...", e)
        from huggingface_hub import hf_hub_download
        return hf_hub_download(
            repo_id=repo,
            filename=filename,
            local_dir=MODEL_DIR,
            local_dir_use_symlinks=False,
        )


def _setup_chat_formatter(llm) -> None:
    try:
        from llama_cpp import llama_chat_format
        handler = llm.chat_handler or getattr(llm, "_chat_handlers", {}).get(llm.chat_format)
        if handler and getattr(handler, "__closure__", None):
            for c in handler.__closure__:
                formatter = c.cell_contents
                if hasattr(formatter, "template"):
                    class _Formatter:
                        def __init__(self, base):
                            self.base = base
                        def __call__(self, *args, **kwargs):
                            kwargs["enable_thinking"] = _thinking_enabled
                            return self.base(*args, **kwargs)
                    llm.chat_handler = llama_chat_format.chat_formatter_to_chat_completion_handler(_Formatter(formatter))
                    break
    except Exception as e:
        logger.debug("[Chat Formatter Setup] %s", e)


def init_local_llm(force_reload: bool = False):
    global _local_llm_instance, _model_load_failed, _model_load_error, _model_loading, _model_load_failed_at

    if not force_reload:
        if _local_llm_instance is not None:
            return _local_llm_instance
        if _model_load_failed and (time.time() - _model_load_failed_at) < _MODEL_RETRY_SECONDS:
            return None

    with _llm_lock:
        if not force_reload and _local_llm_instance is not None:
            return _local_llm_instance

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
            active_info = AVAILABLE_MODELS.get(_active_model_key, next(iter(AVAILABLE_MODELS.values())))
            try:
                _local_llm_instance = Llama(**kwargs)
                logger.info("[Local AI Engine] Initialized %s on GPU (n_gpu_layers=%s, n_ctx=%s)", active_info["name"], kwargs["n_gpu_layers"], kwargs["n_ctx"])
                print(f"[Local AI Engine] {active_info['name']} is running on GPU: NVIDIA GeForce RTX 3050 (n_gpu_layers={kwargs['n_gpu_layers']}, n_ctx={kwargs['n_ctx']}, flash_attn={kwargs['flash_attn']})", flush=True)
            except Exception as gpu_err:
                logger.warning("[Local AI Engine] GPU load failed (%s), falling back to CPU", gpu_err)
                kwargs["n_gpu_layers"] = 0
                kwargs["offload_kqv"] = False
                kwargs["flash_attn"] = False
                kwargs["type_k"] = 0
                kwargs["type_v"] = 0
                _local_llm_instance = Llama(**kwargs)

            _setup_chat_formatter(_local_llm_instance)
            _model_load_failed = False
            _model_load_error = None
            return _local_llm_instance
        except Exception as e:
            _model_load_failed = True
            _model_load_failed_at = time.time()
            _model_load_error = str(e)
            logger.error("[Local AI Engine] Load error: %s", e)
            return None


def get_available_models() -> dict:
    for m in AVAILABLE_MODELS.values():
        m["downloaded"] = os.path.exists(m["path"]) and os.path.getsize(m["path"]) > 500_000_000
    return AVAILABLE_MODELS


def set_active_model(model_key: str = "lfm-3-1.7b") -> bool:
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
        "size_gb": m_info.get("size_gb", 2.47),
        "vision_ready": False,
        "accelerator": "NVIDIA(CUDA)" if has_gpu else "CPU",
        "gpu_offload": has_gpu,
        "flash_attn": has_gpu,
    }


# ── Text & Response Cleaning ────────────────────────────────────

def _strip_reasoning(text: str, fallback_to_thought: bool = False) -> tuple[str, str]:
    if not text:
        return "", ""
    thoughts: list[str] = []
    clean = text

    # 1. Handle orphan </think> or standard <think>...</think> blocks
    if "</think>" in clean.lower():
        parts = re.split(r"</think>", clean, flags=re.IGNORECASE)
        for part in parts[:-1]:
            t = re.sub(r"<think>", "", part, flags=re.IGNORECASE).strip()
            if t:
                thoughts.append(t)
        clean = parts[-1].strip()
    elif "<think>" in clean.lower():
        parts = re.split(r"<think>", clean, flags=re.IGNORECASE)
        clean = parts[0].strip()
        for part in parts[1:]:
            t = part.strip()
            if t:
                thoughts.append(t)

    clean = _RE_SPEAKER_PREFIX.sub("", clean).strip()
    if not clean and fallback_to_thought and thoughts:
        clean = thoughts[-1]
    return clean, "\n".join(thoughts)


def sanitize_for_tts(text: str) -> str:
    """Clean and normalize output text for speech synthesis."""
    if not text:
        return ""
    try:
        from amigo.utils.tts import normalize_text_for_tts
        return normalize_text_for_tts(text)
    except Exception:
        clean, _ = _strip_reasoning(text)
        return clean.strip()


def strip_markdown_for_tts(text: str) -> str:
    return sanitize_for_tts(text)


def clean_tts_text(text: str) -> str:
    return sanitize_for_tts(text)


def _prepare_chat_messages(
    system_prompt: str,
    prompt: str | list[dict],
) -> list["ChatCompletionRequestMessage"]:
    if isinstance(prompt, list):
        if prompt and prompt[0].get("role") == "system":
            return cast(list["ChatCompletionRequestMessage"], prompt)
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + prompt
        return cast(list["ChatCompletionRequestMessage"], messages)
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": str(prompt)}]


# ── Core Inference APIs ─────────────────────────────────────────

def query_local_llm(
    prompt: str | list[dict],
    system_prompt: str = _STATIC_VOICE_SYSTEM_PROMPT,
    max_tokens: int = 512,
    temperature: float | None = None,
    thinking: bool | None = None,
    sanitize: bool = False,
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

    try:
        sampling = get_sampling_params()
        kwargs = {
            "messages": _prepare_chat_messages(system_prompt, prompt),
            "max_tokens": max_tokens,
            "temperature": sampling["temperature"] if temperature is None else temperature,
            "top_p": sampling["top_p"],
            "min_p": sampling.get("min_p", 0.05) if min_p is None else min_p,
            "repeat_penalty": 1.05 if repeat_penalty is None else repeat_penalty,
            "stop": STOP_TOKENS if stop is None else stop,
        }

        with _inference_lock:
            try:
                res = llm.create_chat_completion(**kwargs, stream=False)
            except Exception as e:
                if "exceed context window" in str(e).lower() and kwargs.get("max_tokens", 0) > 128:
                    kwargs["max_tokens"] = 128
                    res = llm.create_chat_completion(**kwargs, stream=False)
                else:
                    raise

        if not isinstance(res, dict):
            raise TypeError("LLM returned stream for non-streaming query")
        output = (res["choices"][0]["message"]["content"] or "").strip()
        clean = _RE_SPEAKER_PREFIX.sub("", output).strip()
        return (sanitize_for_tts(clean) or clean) if sanitize else clean
    except Exception as e:
        logger.error("[LLM Query Error]: %s", e)
        if raise_on_error:
            raise
        return "I'm having trouble processing that request."


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

    try:
        sampling = get_sampling_params()
        temp = sampling["temperature"] if temperature is None else temperature
        with _inference_lock:
            stream_res = None
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
                if isinstance(stream_res, dict):
                    raise TypeError("LLM returned non-stream response for streaming query")
                for chunk in stream_res:
                    if interruption_event and interruption_event.is_set():
                        break
                    if delta := chunk["choices"][0]["delta"].get("content"):
                        yield delta
            except Exception as e:
                if "exceed context window" in str(e).lower() and max_tokens > 128:
                    stream_res = llm.create_chat_completion(
                        messages=_prepare_chat_messages(system_prompt, prompt),
                        max_tokens=128,
                        temperature=temp,
                        top_p=sampling["top_p"],
                        repeat_penalty=1.05,
                        stop=STOP_TOKENS,
                        stream=True,
                    )
                    for chunk in stream_res:
                        if interruption_event and interruption_event.is_set():
                            break
                        if delta := chunk["choices"][0]["delta"].get("content"):
                            yield delta
                else:
                    logger.error("[LLM Stream Error]: %s", e)
                    yield "I'm having trouble processing that request."
            finally:
                if close_stream := getattr(stream_res, "close", None):
                    try:
                        close_stream()
                    except Exception:
                        pass
    except Exception as e:
        logger.error("[LLM Stream Init Error]: %s", e)
        yield "I'm having trouble processing that request."


_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_RE_ABBREV = re.compile(r"\b(mr|mrs|ms|dr|vs|eg|ie|etc|prof|sr|jr|st)\.$", re.I)


def stream_sentence_chunks(
    token_generator,
    interruption_event: threading.Event | None = None,
    thinking_enabled: bool = False,
) -> Generator[str, None, None]:
    """Yield sentence-level chunks suitable for real-time TTS and UI streaming."""
    buffer = ""
    in_think = thinking_enabled

    for token in token_generator:
        if interruption_event and interruption_event.is_set():
            if hasattr(token_generator, "close"):
                token_generator.close()
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
        elif "</think>" in buffer:
            buffer = buffer.split("</think>", 1)[1]

        splits = _RE_SENTENCE_SPLIT.split(buffer)
        if len(splits) > 1:
            for s in splits[:-1]:
                cand = s.strip()
                if cand and not _RE_ABBREV.search(cand):
                    clean = _RE_SPEAKER_PREFIX.sub("", cand).strip()
                    if clean:
                        yield clean
            buffer = splits[-1]

    if buffer.strip() and not (interruption_event and interruption_event.is_set()):
        clean = _RE_SPEAKER_PREFIX.sub("", buffer.strip()).strip()
        if "</think>" in clean:
            clean = clean.split("</think>")[-1].strip()
        if clean and not (in_think and not clean):
            yield clean


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
    limit = max_tokens or (1024 if thinking_active else 512)
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
    # Return pristine response with full formatting for UI display and memory.
    # Note: TTS speech is normalized separately inside tts.speak() on its own copy.
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
    max_tokens = 1024 if thinking_active else 512
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


def query_local_vision(*args, **kwargs) -> str | None:
    return None


# ── Backward-Compatibility Forwarders ──────────────────────────

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


if __name__ == "__main__":
    from amigo.ui.server import launch_server, initialize_amigo_async

    _info = get_active_model_info()
    print("==================================================")
    print("   AMIGO VOICE ASSISTANT - UI DASHBOARD SERVER   ")
    print(f"   [ {_info['name'].upper()} | {_info.get('tts_engine', 'KOKORO ONNX').upper()} | RAG + AGENTIC ]")
    print("==================================================")
    initialize_amigo_async()
    launch_server(port=5000, open_browser=True)

