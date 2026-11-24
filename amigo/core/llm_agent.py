"""
LLM-Based Agent for Amigo Voice Assistant.
- Direct GGUF inference via llama-cpp-python on GPU (with CPU fallback)
- Fast KV-cache reuse with static system prompt prefix and formatter state caching
- Multi-turn conversation context (single & multi-step) and RAG profile integration
- Real-time, non-destructive streaming sentence synthesis for TTS
"""

from __future__ import annotations

import atexit
import datetime
import gc
import json
import logging
import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator, cast

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from llama_cpp.llama_types import ChatCompletionRequestMessage

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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_cuda_dirs_setup: bool = False
_cuda_setup_lock = threading.Lock()


def _setup_cuda_dll_directory() -> None:
    """Ensure CUDA and cuBLAS runtime DLLs as well as llama_cpp backends are accessible on Windows."""
    global _cuda_dirs_setup
    if _cuda_dirs_setup or sys.platform != "win32":
        return

    with _cuda_setup_lock:
        if _cuda_dirs_setup:
            return
        _cuda_dirs_setup = True

        try:
            import site

            sp_list = [
                *(site.getsitepackages() or []),
                site.getusersitepackages(),
                sys.prefix,
                os.path.join(sys.prefix, "Lib", "site-packages"),
            ]
            cuda_dirs = [
                os.path.join(sp, "nvidia", sub, "bin")
                for sp in sp_list
                if isinstance(sp, str)
                for sub in ("cuda_runtime", "cublas", "cuda_nvrtc")
            ]
            for env_key, env_val in os.environ.items():
                if env_key.startswith(("CUDA_PATH", "CUDA_HOME")) and env_val:
                    cuda_dirs.append(os.path.join(env_val, "bin"))

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
                except Exception as exc:
                    logger.debug("[CUDA Setup] Unable to inspect llama_cpp lib directory: %s", exc)

            path_env = os.environ.get("PATH", "")
            for d in cuda_dirs:
                if os.path.isdir(d):
                    try:
                        os.add_dll_directory(d)
                    except OSError as exc:
                        logger.debug("[CUDA Setup] add_dll_directory failed for %s: %s", d, exc)
                    if d not in path_env:
                        path_env = d + os.pathsep + path_env
            os.environ["PATH"] = path_env

            try:
                import ctypes

                active_lib = os.environ.get("LLAMA_CPP_LIB_PATH")
                if not active_lib:
                    import llama_cpp

                    active_lib = os.path.join(os.path.dirname(llama_cpp.__file__), "lib")
                base_dll = os.path.join(active_lib, "ggml-base.dll")
                if os.path.exists(base_dll):
                    try:
                        ctypes.CDLL(base_dll)
                    except OSError as exc:
                        logger.debug("[CUDA Setup] Preload failed for ggml-base.dll: %s", exc)
                ggml_dll = os.path.join(active_lib, "ggml.dll")
                if os.path.exists(ggml_dll):
                    ggml = ctypes.CDLL(ggml_dll)
                    if hasattr(ggml, "ggml_backend_load_all_from_path"):
                        ggml.ggml_backend_load_all_from_path(active_lib.encode("utf-8"))
                    elif hasattr(ggml, "ggml_backend_load_all"):
                        ggml.ggml_backend_load_all()
            except Exception as exc:
                logger.debug("[CUDA Setup] Backend plugin preload warning: %s", exc)
        except Exception as exc:
            logger.warning("[CUDA Setup] DLL directory initialization encountered an error: %s", exc)


import amigo.core.rag_engine as rag_engine
from amigo.utils.network_utils import is_internet_connected

logger = logging.getLogger("amigo.llm_agent")
_setup_cuda_dll_directory()

# Lock ordering hierarchy: _llm_lock -> _CONFIG_LOCK -> _inference_lock
_thread_local = threading.local()
_inference_lock = threading.Lock()
_llm_lock = threading.Lock()
_CONFIG_LOCK = threading.Lock()
_internet_cache_lock = threading.Lock()
_screen_state_lock = threading.Lock()
_mode_lock = threading.Lock()


# ── Typed Configuration Models (Pydantic v2) ───────────────────

class AgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_dir: str = Field(
        default_factory=lambda: os.path.abspath(
            os.getenv("AMIGO_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "models"))
        )
    )
    active_model_key: str = Field(default_factory=lambda: os.getenv("AMIGO_ACTIVE_MODEL", "qwen3.5-4b-instruct"))
    default_repo: str = Field(default_factory=lambda: os.getenv("AMIGO_MODEL_REPO", "unsloth/Qwen3.5-4B-GGUF"))
    default_filename: str = Field(default_factory=lambda: os.getenv("AMIGO_MODEL_FILENAME", "Qwen3.5-4B-Q4_K_M.gguf"))
    min_model_bytes: int = Field(default_factory=lambda: _env_int("AMIGO_MIN_MODEL_BYTES", 500_000_000))
    min_mmproj_bytes: int = Field(default_factory=lambda: _env_int("AMIGO_MIN_MMPROJ_BYTES", 50_000_000))
    model_retry_seconds: float = Field(default_factory=lambda: _env_float("AMIGO_MODEL_RETRY_SECONDS", 60.0))
    internet_cache_ttl: float = Field(default_factory=lambda: _env_float("AMIGO_INTERNET_CACHE_TTL", 30.0))
    profile_cache_ttl: float = Field(default_factory=lambda: _env_float("AMIGO_PROFILE_CACHE_TTL", 60.0))
    turn_context_ttl: float = Field(default_factory=lambda: _env_float("AMIGO_TURN_CONTEXT_TTL", 30.0))
    screen_text_ttl: float = Field(default_factory=lambda: _env_float("AMIGO_SCREEN_TEXT_TTL", 60.0))
    screen_text_max_chars: int = Field(default_factory=lambda: _env_int("AMIGO_SCREEN_TEXT_MAX_CHARS", 1500))
    clipboard_max_chars: int = Field(default_factory=lambda: _env_int("AMIGO_CLIPBOARD_MAX_CHARS", 1000))
    voice_max_tokens: int = Field(default_factory=lambda: _env_int("AMIGO_VOICE_MAX_TOKENS", 320))
    text_max_tokens: int = Field(default_factory=lambda: _env_int("AMIGO_TEXT_MAX_TOKENS", 1024))
    thinking_max_tokens: int = Field(default_factory=lambda: _env_int("AMIGO_THINKING_MAX_TOKENS", 1024))
    stream_max_tokens: int = Field(default_factory=lambda: _env_int("AMIGO_STREAM_MAX_TOKENS", 512))


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    repo: str
    filename: str
    path: str
    targets: tuple[tuple[str, str], ...] = ()
    size_gb: float = 2.55
    mmproj_path: str = ""


SETTINGS = AgentSettings()

_internet_status_cache: dict[str, Any] = {
    "status": "Connected (Online)",
    "online": True,
    "timestamp": 0.0,
}

_RE_SPEAKER_PREFIX = re.compile(r"^(Amigo|Assistant|AI):\s*", flags=re.IGNORECASE)
_RE_THINK_OPEN = re.compile(r"<think>", flags=re.IGNORECASE)
_RE_THINK_CLOSE = re.compile(r"</think>", flags=re.IGNORECASE)

_last_screen_text = ""
_last_screen_time = 0.0

_STATIC_VOICE_SYSTEM_PROMPT = (
    "You are Amigo, a friendly, intelligent, and capable personal AI voice assistant on the user's Windows PC.\n"
    "Core Instructions:\n"
    "- State the direct answer or solution immediately in the first sentence.\n"
    "- In [Mode: Voice], keep responses conversational and concise (1-3 spoken sentences) unless the user specifically asks for detailed explanations, lists, or steps.\n"
    "- In [Mode: Text] and [Mode: Document Analysis], give thorough, well-structured, complete answers with clear explanations, relevant details, and formatted steps. Use standard markdown with proper newlines between bullet points (- item) and numbered steps. Never collapse multiple points or steps onto a single line.\n"
    "- For multiple-choice questions or numerical problems, clearly state the final answer and option letter in bold on its own line, followed by distinct bullet points or numbered calculation steps.\n"
    "- Answer ONLY the user's current question. Never bring up past topics, previous commands, active links, or background media unless the user specifically asks about them.\n"
    "- Speak naturally and confidently. Never use robotic filler phrases like 'Certainly!', 'Sure thing!', 'As an AI', or 'According to my knowledge'.\n"
    "- Never mention internal tool names, function names, commands with underscores, or system schemas in your responses; speak naturally.\n"
    "- Never deflect with multi-choice question menus. Answer directly with what you know.\n"
    "- Never claim you opened, launched, changed, or executed an OS/file/app action unless a tool actually ran and reported that result.\n"
    "- Only reference 'available documents' or 'in your files' if the user specifically asked about their personal files.\n"
    "- For riddles, physics puzzles, and logical word problems, reason carefully through physical space, geometry, and real-world mechanics before giving the direct answer.\n"
    "- Resolve pronouns ('it', 'that', 'they', 'he', 'she', 'there') and follow-up questions naturally from conversation history and active environment state."
)


def get_static_system_prompt() -> str:
    """Conversational assistant system prompt for natural speech and text responses."""
    return _STATIC_VOICE_SYSTEM_PROMPT


def _get_internet_status() -> tuple[str, bool]:
    now = time.time()
    with _internet_cache_lock:
        if now - float(_internet_status_cache["timestamp"]) <= SETTINGS.internet_cache_ttl:
            return str(_internet_status_cache["status"]), bool(_internet_status_cache["online"])

    online = is_internet_connected()
    status = "Connected (Online)" if online else "Disconnected (Offline)"
    with _internet_cache_lock:
        _internet_status_cache["status"] = status
        _internet_status_cache["online"] = online
        _internet_status_cache["timestamp"] = now
    return status, online


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
        with _screen_state_lock:
            _last_screen_text = text.strip()[: SETTINGS.screen_text_max_chars]
            _last_screen_time = time.time()


def get_last_screen_text(consume: bool = False) -> str:
    global _last_screen_text, _last_screen_time
    with _screen_state_lock:
        if not _last_screen_text or (time.time() - _last_screen_time > SETTINGS.screen_text_ttl):
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
                return text[: SETTINGS.clipboard_max_chars].strip()
        except Exception as exc:
            logger.debug("[Clipboard] Unable to read clipboard text: %s", exc)
    return None


_turn_context_cache: tuple[float, tuple, str] | None = None
_turn_context_lock = threading.Lock()
_profile_prompt_cache: tuple[float, str] | None = None
_profile_prompt_lock = threading.Lock()


def _get_cached_user_profile() -> str:
    """Cache the non-semantic user profile prompt to avoid per-turn vector lookups."""
    global _profile_prompt_cache
    now = time.time()
    with _profile_prompt_lock:
        if _profile_prompt_cache is not None:
            c_time, c_val = _profile_prompt_cache
            if now - c_time < SETTINGS.profile_cache_ttl:
                return c_val
    try:
        val = rag_engine.get_user_profile_prompt("", semantic_search=False) or ""
    except Exception as exc:
        logger.debug("[Profile Cache] Failed to load profile prompt: %s", exc)
        val = ""
    with _profile_prompt_lock:
        _profile_prompt_cache = (now, val)
    return val


def build_turn_runtime_context(
    query: str,
    web_context: str = "",
    doc_context: str = "",
    pending_confirmation: str = "",
) -> str:
    global _turn_context_cache
    now_ts = time.time()

    ctx_blocks: list[str] = []
    now = datetime.datetime.now()
    ctx_blocks.append(f"[Current Time: {now.strftime('%A, %B %d, %Y %I:%M %p').lstrip('0')} ({_day_period(now.hour)})]")

    if pending_confirmation:
        ctx_blocks.append(pending_confirmation)

    active_ctx = ""
    try:
        active_ctx = rag_engine.get_active_context_prompt() or ""
    except Exception as exc:
        logger.debug("[Runtime Context] Active context unavailable: %s", exc)
    if active_ctx:
        ctx_blocks.append(active_ctx)

    state: dict = {}
    try:
        state = rag_engine.get_active_state(clean_expired=False) or {}
    except Exception as exc:
        logger.debug("[Runtime Context] Active state unavailable: %s", exc)

    if gen_info := state.get("generated_content"):
        if isinstance(gen_info, dict) and gen_info.get("content"):
            ctx_blocks.append(f"[Active Draft: {gen_info.get('type', 'content')} about '{gen_info.get('topic', '')}']")

    recent_items = state.get("found_files") or state.get("recent_items") or []
    if isinstance(recent_items, dict):
        recent_items = recent_items.get("items", [])
    item_lines: list[str] = []
    if isinstance(recent_items, list) and recent_items:
        for i, it in enumerate(recent_items[:10]):
            val = os.path.basename(str(it)) if os.path.isabs(str(it)) else str(it)
            item_lines.append(f"  [{i}]: {val}")
        ctx_blocks.append("[Recent Results List (0=first, 1=second, ..., -1=last)]:\n" + "\n".join(item_lines))

    user_prof = _get_cached_user_profile()
    if user_prof:
        ctx_blocks.append(f"[User Profile & Memory]:\n{user_prof}")

    screen = get_last_screen_text()
    if screen:
        ctx_blocks.append(f"[Screen OCR]: {screen[:300]}")

    if web_context:
        ctx_blocks.append(f"[Live Web Search Results]:\n{web_context}")
    if doc_context:
        ctx_blocks.append(f"[Retrieved Local Context]:\n{doc_context}")

    res = "\n\n".join(ctx_blocks)
    cache_key = (
        query.strip(),
        web_context,
        doc_context,
        pending_confirmation.strip(),
        active_ctx,
        user_prof,
        screen[:300],
        tuple(item_lines),
    )

    with _turn_context_lock:
        if _turn_context_cache is not None:
            c_time, c_key, c_val = _turn_context_cache
            if (now_ts - c_time < SETTINGS.turn_context_ttl) and (c_key == cache_key):
                return c_val
        _turn_context_cache = (now_ts, cache_key, res)
    return res


def build_unified_history_messages(
    conversation_history: list | None = None,
    max_turns: int = 3,
    for_router: bool = False,
) -> list[dict]:
    history = conversation_history or rag_engine.get_recent_conversations(count=max_turns)
    formatted: list[dict] = []
    for turn in history[-max_turns:]:
        u = str(turn.get("user") or turn.get("user_query") or turn.get("query") or "").strip()
        a = str(turn.get("assistant") or turn.get("assistant_reply") or turn.get("response") or "").strip()
        tool = str(turn.get("tool") or "").strip()
        params = turn.get("params")
        actions = turn.get("actions")
        if u:
            clean_a, _ = _strip_reasoning(a) if a else ("", "")
            spoken = (clean_a.strip() or a)[:400]
            formatted.append({"role": "user", "content": u[:300]})
            if for_router:
                if isinstance(actions, list) and actions:
                    clean_actions = [
                        {"tool": act.get("tool"), "params": act.get("params", {})}
                        for act in actions
                        if isinstance(act, dict) and act.get("tool")
                    ]
                    if clean_actions:
                        formatted.append({"role": "assistant", "content": json.dumps({"actions": clean_actions})})
                        continue
                if tool and tool != "chat" and isinstance(params, dict):
                    formatted.append({"role": "assistant", "content": json.dumps({"actions": [{"tool": tool, "params": params}]})})
                    continue
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
    if doc_context and doc_context.strip().startswith("[Attached "):
        use_memory = False

    messages: list[dict] = [{"role": "system", "content": get_static_system_prompt()}]
    if use_memory:
        history_count = 3 if is_voice else 5
        messages.extend(build_unified_history_messages(conversation_history=conversation_history, max_turns=history_count))

    runtime_ctx = build_turn_runtime_context(query, web_context=web_context, doc_context=doc_context)
    if doc_context:
        mode_tag = (
            "[Mode: Document Analysis | Answer the question directly and concisely using ONLY the document that matches the user's specific request. "
            "If multiple documents are provided in the context, focus strictly on the document relevant to the inquiry and ignore irrelevant files. "
            "Do NOT include unrelated numbers, IDs, or details from other files.]"
        )
    else:
        mode_tag = "[Mode: Voice | Output: Direct Spoken Answer]" if is_voice else "[Mode: Text | Output: Direct Formatted Answer]"
    user_content = f"{runtime_ctx}\n\nUser Question: {query}\n{mode_tag}" if runtime_ctx else f"{query}\n{mode_tag}"
    messages.append({"role": "user", "content": user_content})
    return messages


# ── Model Configuration & Lifecycle ────────────────────────────

MODEL_DIR = SETTINGS.model_dir
MODEL_NAME = "Qwen 3.5 4B Instruct"
MODEL_FILENAME = SETTINGS.default_filename
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILENAME)
MODEL_TARGETS = [
    (SETTINGS.default_repo, SETTINGS.default_filename),
    ("lmstudio-community/Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf"),
    ("openresearchtools/Qwen3.5-4B-Instruct-GGUF", "Q4_K_M.gguf"),
]

STOP_TOKENS = ["<eos>", "<turn|>", "<|turn|>", "<|endoftext|>", "<|im_end|>", "<|im_start|>", "<|eot_id|>"]

_MODEL_REGISTRY: dict[str, ModelSpec] = {
    SETTINGS.active_model_key: ModelSpec(
        key=SETTINGS.active_model_key,
        name=MODEL_NAME,
        repo=SETTINGS.default_repo,
        filename=MODEL_FILENAME,
        path=MODEL_PATH,
        targets=tuple(MODEL_TARGETS),
        size_gb=2.55,
    ),
}


def _is_valid_model_file(path: str, min_bytes: int | None = None) -> bool:
    threshold = SETTINGS.min_model_bytes if min_bytes is None else min_bytes
    try:
        return bool(path and os.path.isfile(path) and os.path.getsize(path) >= threshold)
    except OSError:
        return False


def _discover_models() -> dict[str, ModelSpec]:
    specs = dict(_MODEL_REGISTRY)
    if os.path.isdir(MODEL_DIR):
        try:
            for entry in os.scandir(MODEL_DIR):
                if entry.is_file() and entry.name.lower().endswith(".gguf") and "mmproj" not in entry.name.lower():
                    key = Path(entry.name).stem.lower()
                    if key not in specs and all(s.path != entry.path for s in specs.values()):
                        size_gb = round(entry.stat().st_size / (1024**3), 2)
                        specs[key] = ModelSpec(
                            key=key,
                            name=Path(entry.name).stem.replace("-", " "),
                            repo="",
                            filename=entry.name,
                            path=entry.path,
                            size_gb=size_gb,
                        )
        except OSError as exc:
            logger.debug("[Model Discovery] Could not scan %s: %s", MODEL_DIR, exc)
    return specs


AVAILABLE_MODELS: dict[str, dict[str, Any]] = {
    k: {**v.model_dump(), "targets": list(v.targets), "downloaded": _is_valid_model_file(v.path)}
    for k, v in _MODEL_REGISTRY.items()
}

_active_model_key = SETTINGS.active_model_key
_local_llm_instance = None
_applied_thinking_state: bool | None = None
_model_load_failed = False
_model_load_error = None
_model_load_failed_at = 0.0
_PHYSICAL_CORES: int | None = None
_LLM_CONFIG: dict[str, Any] | None = None

_thinking_enabled: bool = _env_bool("AMIGO_THINKING_ENABLED", False)
_creativity_enabled: bool = _env_bool("AMIGO_CREATIVITY_ENABLED", True)


def set_thinking_enabled(enabled: bool) -> None:
    global _thinking_enabled
    with _mode_lock:
        _thinking_enabled = bool(enabled)


def is_thinking_enabled() -> bool:
    with _mode_lock:
        return _thinking_enabled


def get_current_thinking_enabled() -> bool:
    return is_thinking_enabled()


def set_creativity_enabled(enabled: bool) -> None:
    global _creativity_enabled
    with _mode_lock:
        _creativity_enabled = bool(enabled)


def is_creativity_enabled() -> bool:
    with _mode_lock:
        return _creativity_enabled


def get_sampling_params() -> dict[str, float]:
    creative = is_creativity_enabled()
    return {
        "temperature": 0.3 if creative else 0.15,
        "top_p": 0.95,
        "min_p": 0.05,
        "repeat_penalty": 1.05,
    }


def _get_physical_cores() -> int:
    global _PHYSICAL_CORES
    if _PHYSICAL_CORES is None:
        try:
            import psutil

            _PHYSICAL_CORES = psutil.cpu_count(logical=False) or os.cpu_count() or 4
        except Exception as exc:
            logger.debug("[CPU Cores] psutil unavailable, falling back to os.cpu_count(): %s", exc)
            _PHYSICAL_CORES = os.cpu_count() or 4
    return _PHYSICAL_CORES


def _get_llm_config() -> dict[str, Any]:
    global _LLM_CONFIG
    with _CONFIG_LOCK:
        if _LLM_CONFIG is not None:
            return _LLM_CONFIG
        has_gpu = False
        try:
            import llama_cpp

            has_gpu = bool(llama_cpp.llama_supports_gpu_offload())
        except Exception as exc:
            logger.debug("[LLM Config] Failed to query GPU offload support: %s", exc)
        cores = _get_physical_cores()
        default_threads = min(4, max(2, cores // 2)) if has_gpu else max(1, cores - 1)
        n_gpu_layers = _env_int("AMIGO_LLM_N_GPU_LAYERS", -1 if has_gpu else 0)
        gpu_active = has_gpu and n_gpu_layers != 0
        _LLM_CONFIG = {
            "n_ctx": _env_int("AMIGO_LLM_N_CTX", 8192),
            "n_threads": _env_int("AMIGO_LLM_N_THREADS", default_threads),
            "n_threads_batch": _env_int("AMIGO_LLM_N_THREADS_BATCH", cores),
            "n_batch": _env_int("AMIGO_LLM_N_BATCH", 512),
            "n_ubatch": _env_int("AMIGO_LLM_N_UBATCH", 256),
            "n_gpu_layers": n_gpu_layers,
            "use_mmap": _env_bool("AMIGO_LLM_USE_MMAP", True),
            "logits_all": False,
            "embedding": False,
            "offload_kqv": gpu_active,
            "flash_attn": gpu_active,
            "type_k": 2 if gpu_active else 0,
            "type_v": 2 if gpu_active else 0,
            "numa": False,
        }
    return _LLM_CONFIG


def _download_hf_file(targets: list[tuple[str, str]], target_path: str, min_size: int, label: str) -> str:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if _is_valid_model_file(target_path, min_size):
        return target_path

    for repo, fname in targets:
        try:
            from huggingface_hub import hf_hub_download

            downloaded = hf_hub_download(repo_id=repo, filename=fname, local_dir=MODEL_DIR)
            if _is_valid_model_file(downloaded, min_size):
                if downloaded != target_path and not os.path.exists(target_path):
                    os.replace(downloaded, target_path)
                return target_path
        except Exception as e:
            logger.warning("Download attempt failed for %s/%s (%s): %s", repo, fname, label, e)
    return target_path


def get_model_path() -> str:
    models = _discover_models()
    active_spec = models.get(_active_model_key) or next(iter(models.values()))
    if _is_valid_model_file(active_spec.path):
        return active_spec.path

    if active_spec.targets:
        downloaded = _download_hf_file(
            list(active_spec.targets),
            active_spec.path,
            SETTINGS.min_model_bytes,
            active_spec.name,
        )
        if _is_valid_model_file(downloaded):
            return downloaded

    for spec in models.values():
        if _is_valid_model_file(spec.path):
            logger.info("[Local AI Engine] Falling back to available model on disk: %s", spec.name)
            return spec.path

    return active_spec.path


def is_vision_ready() -> bool:
    models = _discover_models()
    active_spec = models.get(_active_model_key)
    mmproj = active_spec.mmproj_path if active_spec else ""
    return _is_valid_model_file(mmproj, SETTINGS.min_mmproj_bytes)


def ensure_model_downloaded() -> str:
    path = get_model_path()
    if _is_valid_model_file(path):
        return path
    models = _discover_models()
    spec = models.get(_active_model_key) or next(iter(models.values()))
    repo = spec.repo or SETTINGS.default_repo
    filename = spec.filename or SETTINGS.default_filename
    logger.info("Downloading %s (%s) to %s...", spec.name, filename, path)
    os.makedirs(MODEL_DIR, exist_ok=True)
    try:
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        subprocess.run(
            ["curl.exe", "-4", "-L", "-C", "-", "--retry", "5", "--retry-delay", "2", "-o", path, url],
            check=True,
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


def _setup_chat_formatter(llm, thinking_override: bool | None = None) -> None:
    global _applied_thinking_state
    target_thinking = is_thinking_enabled() if thinking_override is None else bool(thinking_override)
    if _applied_thinking_state is target_thinking:
        return

    try:
        from llama_cpp import llama_chat_format

        base_formatter = getattr(llm, "_amigo_base_formatter", None)
        if base_formatter is None:
            handler = llm.chat_handler or getattr(llm, "_chat_handlers", {}).get(llm.chat_format)
            if handler and getattr(handler, "__closure__", None):
                for c in handler.__closure__:
                    formatter = c.cell_contents
                    if hasattr(formatter, "template"):
                        base_formatter = formatter
                        llm._amigo_base_formatter = base_formatter
                        break

        if base_formatter is not None:

            class _Formatter:
                def __init__(self, base, enable_thinking: bool):
                    self.base = base
                    self.enable_thinking = enable_thinking

                def __call__(self, *args, **kwargs):
                    kwargs["enable_thinking"] = self.enable_thinking
                    return self.base(*args, **kwargs)

            llm.chat_handler = llama_chat_format.chat_formatter_to_chat_completion_handler(
                _Formatter(base_formatter, target_thinking)
            )
        _applied_thinking_state = target_thinking
    except Exception as e:
        logger.debug("[Chat Formatter Setup] %s", e)


def _release_llm_instance_locked() -> None:
    """Caller must hold _llm_lock and _CONFIG_LOCK (and _inference_lock when mutating active instance)."""
    global _local_llm_instance, _LLM_CONFIG, _applied_thinking_state
    if _local_llm_instance is not None:
        try:
            if hasattr(_local_llm_instance, "close"):
                _local_llm_instance.close()
        except Exception as e:
            logger.debug("[Local AI Engine] Model close exception: %s", e)
        try:
            del _local_llm_instance
        except Exception as e:
            logger.debug("[Local AI Engine] Model delete exception: %s", e)
        _local_llm_instance = None
    _LLM_CONFIG = None
    _applied_thinking_state = None


def init_local_llm(force_reload: bool = False):
    global _local_llm_instance, _model_load_failed, _model_load_error, _model_load_failed_at, _applied_thinking_state

    if not force_reload:
        with _llm_lock:
            if _local_llm_instance is not None:
                return _local_llm_instance
            if _model_load_failed and (time.time() - _model_load_failed_at) < SETTINGS.model_retry_seconds:
                return None

    with _llm_lock:
        if not force_reload and _local_llm_instance is not None:
            return _local_llm_instance

        try:
            model_file = get_model_path()
            if not _is_valid_model_file(model_file):
                _model_load_failed = True
                _model_load_failed_at = time.time()
                _model_load_error = f"Model file not found: {model_file}"
                return None

            from llama_cpp import Llama

            config = dict(_get_llm_config())
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
            models = _discover_models()
            active_spec = models.get(_active_model_key) or next(iter(models.values()))
            try:
                instance = Llama(**kwargs)
                device_label = "GPU offload" if kwargs["n_gpu_layers"] != 0 else "CPU"
                logger.info(
                    "[Local AI Engine] Initialized %s (%s, n_gpu_layers=%s, n_ctx=%s)",
                    active_spec.name,
                    device_label,
                    kwargs["n_gpu_layers"],
                    kwargs["n_ctx"],
                )
            except Exception as gpu_err:
                logger.warning("[Local AI Engine] GPU load failed (%s), falling back to CPU", gpu_err)
                kwargs["n_gpu_layers"] = 0
                kwargs["offload_kqv"] = False
                kwargs["flash_attn"] = False
                kwargs["type_k"] = 0
                kwargs["type_v"] = 0
                instance = Llama(**kwargs)

            _applied_thinking_state = None
            _setup_chat_formatter(instance)
            with _inference_lock:
                _local_llm_instance = instance
            _model_load_failed = False
            _model_load_error = None
            return _local_llm_instance
        except Exception as e:
            _model_load_failed = True
            _model_load_failed_at = time.time()
            _model_load_error = str(e)
            logger.error("[Local AI Engine] Load error: %s", e)
            return None


def warmup_router_prefix() -> float:
    """Pre-evaluates the static router prefix into KV cache so first turn is fast."""
    from amigo.utils.tool_registry import _ROUTER_STOP, get_static_master_prompt

    t0 = time.perf_counter()
    init_local_llm()
    try:
        query_local_llm(
            [{"role": "system", "content": get_static_master_prompt()}, {"role": "user", "content": "hi"}],
            system_prompt="",
            max_tokens=1,
            temperature=0.0,
            thinking=False,
            sanitize=False,
            stop=_ROUTER_STOP,
        )
    except Exception as e:
        logger.warning("[Local AI Engine] Prefix warm-up failed: %s", e)
    elapsed = time.perf_counter() - t0
    logger.info("[Local AI Engine] Router prefix KV cache pre-warmed in %.2f s", elapsed)
    return elapsed


def get_available_models() -> dict[str, dict[str, Any]]:
    models = _discover_models()
    snapshot: dict[str, dict[str, Any]] = {}
    for k, spec in models.items():
        snapshot[k] = {
            **spec.model_dump(),
            "targets": list(spec.targets),
            "downloaded": _is_valid_model_file(spec.path),
        }
    AVAILABLE_MODELS.clear()
    AVAILABLE_MODELS.update(snapshot)
    return snapshot


def _cleanup_llm_model() -> None:
    with _llm_lock:
        with _CONFIG_LOCK:
            with _inference_lock:
                _release_llm_instance_locked()
    try:
        gc.collect()
    except Exception as exc:
        logger.debug("[Local AI Engine] GC exception on cleanup: %s", exc)


atexit.register(_cleanup_llm_model)


def set_active_model(model_key: str | None = None) -> bool:
    global _active_model_key
    target_key = model_key or SETTINGS.active_model_key
    models = _discover_models()
    if target_key not in models:
        return False
    with _llm_lock:
        with _CONFIG_LOCK:
            with _inference_lock:
                _active_model_key = target_key
                _release_llm_instance_locked()
    try:
        gc.collect()
    except Exception as exc:
        logger.debug("[Local AI Engine] GC exception on model switch: %s", exc)
    return init_local_llm(force_reload=True) is not None


def get_active_model_info() -> dict[str, Any]:
    models = _discover_models()
    spec = models.get(_active_model_key) or next(iter(models.values()))
    has_gpu = False
    try:
        import llama_cpp

        has_gpu = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception as exc:
        logger.debug("[Model Info] Failed to check GPU offload: %s", exc)
    return {
        "key": spec.key,
        "name": spec.name,
        "filename": spec.filename,
        "path": spec.path,
        "downloaded": _is_valid_model_file(spec.path),
        "size_gb": spec.size_gb,
        "vision_ready": is_vision_ready(),
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

    if _RE_THINK_CLOSE.search(clean):
        parts = _RE_THINK_CLOSE.split(clean)
        for part in parts[:-1]:
            t = _RE_THINK_OPEN.sub("", part).strip()
            if t:
                thoughts.append(t)
        clean = parts[-1].strip()
    elif _RE_THINK_OPEN.search(clean):
        parts = _RE_THINK_OPEN.split(clean)
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
    except Exception as exc:
        logger.debug("[TTS Sanitize] Fallback cleaner used: %s", exc)
        clean, _ = _strip_reasoning(text)
        return clean.strip()


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


def _shrink_messages_for_context(messages: list[dict], max_chars: int = 12000) -> list[dict]:
    """Progressively trim older turns first, then preserve head+tail of the latest turn on context overflow."""
    if not messages:
        return messages
    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs = [dict(m) for m in messages if m.get("role") != "system"]

    while len(other_msgs) > 1 and sum(len(str(m.get("content", ""))) for m in system_msgs + other_msgs) > max_chars:
        other_msgs.pop(0)

    if len(other_msgs) == 1:
        content = str(other_msgs[0].get("content") or "")
        sys_len = sum(len(str(m.get("content", ""))) for m in system_msgs)
        budget = max(2000, max_chars - sys_len)
        if len(content) > budget:
            half = budget // 2
            other_msgs[0]["content"] = f"{content[:half]}\n...[truncated]...\n{content[-half:]}"

    return system_msgs + other_msgs


def _safe_reset_llm(llm) -> None:
    if llm is None:
        return
    try:
        if hasattr(llm, "reset"):
            llm.reset()
    except Exception as exc:
        logger.debug("[LLM Reset] Ignored reset exception: %s", exc)


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
    non_blocking: bool = False,
    response_format: dict[str, Any] | None = None,
) -> str:
    llm = init_local_llm()
    if not llm:
        if raise_on_error:
            raise RuntimeError(_model_load_error or "language model unavailable")
        return "I'm having trouble initializing the AI engine."

    try:
        sampling = get_sampling_params()
        kwargs: dict[str, Any] = {
            "messages": _prepare_chat_messages(system_prompt, prompt),
            "max_tokens": max_tokens,
            "temperature": sampling["temperature"] if temperature is None else temperature,
            "top_p": sampling["top_p"],
            "min_p": sampling["min_p"] if min_p is None else min_p,
            "repeat_penalty": sampling["repeat_penalty"] if repeat_penalty is None else repeat_penalty,
            "stop": STOP_TOKENS if stop is None else stop,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        if non_blocking:
            if not _inference_lock.acquire(blocking=False):
                logger.debug("[LLM] Skipping non-blocking inference: lock is busy.")
                return ""
        else:
            _inference_lock.acquire()

        try:
            active_llm = _local_llm_instance
            if active_llm is None:
                if raise_on_error:
                    raise RuntimeError("language model unloaded during query")
                return "I'm having trouble initializing the AI engine."

            _setup_chat_formatter(active_llm, thinking_override=thinking)
            try:
                res = active_llm.create_chat_completion(**kwargs, stream=False)
            except Exception as e:
                _safe_reset_llm(active_llm)
                if "exceed context window" in str(e).lower():
                    kwargs["messages"] = _shrink_messages_for_context(kwargs["messages"])
                    if kwargs.get("max_tokens", 0) > 128:
                        kwargs["max_tokens"] = 128
                    res = active_llm.create_chat_completion(**kwargs, stream=False)
                else:
                    raise
        finally:
            _inference_lock.release()

        if not isinstance(res, dict):
            raise TypeError("LLM returned stream for non-streaming query")
        output = (res["choices"][0]["message"]["content"] or "").strip()
        clean = _RE_SPEAKER_PREFIX.sub("", output).strip()
        return (sanitize_for_tts(clean) or clean) if sanitize else clean
    except Exception as e:
        logger.error("[LLM Query Error]: %s", e)
        _safe_reset_llm(_local_llm_instance)
        if raise_on_error:
            raise
        return "I'm having trouble processing that request."


def _iter_stream_chunks(
    active_llm,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    top_p: float,
    min_p: float,
    repeat_penalty: float,
    interruption_event: threading.Event | None,
) -> Generator[str, None, None]:
    stream_res = None
    try:
        stream_res = active_llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
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
    finally:
        if stream_res is not None:
            close_stream = getattr(stream_res, "close", None)
            if callable(close_stream):
                try:
                    close_stream()
                except Exception as exc:
                    logger.debug("[LLM Stream] Close warning: %s", exc)


def query_local_llm_stream(
    prompt: str | list[dict],
    system_prompt: str = _STATIC_VOICE_SYSTEM_PROMPT,
    max_tokens: int = 512,
    interruption_event: threading.Event | None = None,
    temperature: float | None = None,
    thinking: bool | None = None,
    min_p: float | None = None,
    repeat_penalty: float | None = None,
) -> Generator[str, None, None]:
    llm = init_local_llm()
    if not llm:
        yield "I'm having trouble initializing the AI engine."
        return

    try:
        sampling = get_sampling_params()
        temp = sampling["temperature"] if temperature is None else temperature
        top_p = sampling["top_p"]
        effective_min_p = sampling["min_p"] if min_p is None else min_p
        rep_pen = sampling["repeat_penalty"] if repeat_penalty is None else repeat_penalty
        prepared_messages = _prepare_chat_messages(system_prompt, prompt)

        with _inference_lock:
            active_llm = _local_llm_instance
            if active_llm is None:
                yield "I'm having trouble initializing the AI engine."
                return

            _setup_chat_formatter(active_llm, thinking_override=thinking)
            try:
                yield from _iter_stream_chunks(
                    active_llm,
                    prepared_messages,
                    max_tokens=max_tokens,
                    temperature=temp,
                    top_p=top_p,
                    min_p=effective_min_p,
                    repeat_penalty=rep_pen,
                    interruption_event=interruption_event,
                )
            except Exception as e:
                _safe_reset_llm(active_llm)
                if "exceed context window" in str(e).lower():
                    shrunk_messages = _shrink_messages_for_context(prepared_messages)
                    yield from _iter_stream_chunks(
                        active_llm,
                        shrunk_messages,
                        max_tokens=min(max_tokens, 128),
                        temperature=temp,
                        top_p=top_p,
                        min_p=effective_min_p,
                        repeat_penalty=rep_pen,
                        interruption_event=interruption_event,
                    )
                else:
                    logger.error("[LLM Stream Error]: %s", e)
                    yield "I'm having trouble processing that request."
    except Exception as e:
        logger.error("[LLM Stream Init Error]: %s", e)
        _safe_reset_llm(_local_llm_instance)
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
    in_think = False

    for token in token_generator:
        if interruption_event and interruption_event.is_set():
            if hasattr(token_generator, "close"):
                try:
                    token_generator.close()
                except Exception as exc:
                    logger.debug("[Sentence Stream] Generator close warning: %s", exc)
            return

        buffer += token
        if _RE_THINK_OPEN.search(buffer):
            in_think = True
        if in_think:
            close_match = _RE_THINK_CLOSE.search(buffer)
            if close_match:
                buffer = buffer[close_match.end() :]
                in_think = False
            else:
                continue
        else:
            close_match = _RE_THINK_CLOSE.search(buffer)
            if close_match:
                buffer = buffer[close_match.end() :]

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
        close_match = _RE_THINK_CLOSE.search(clean)
        if close_match:
            clean = clean[close_match.end() :].strip()
        if clean and not in_think:
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
    limit = max_tokens or (
        SETTINGS.thinking_max_tokens
        if thinking_active
        else (SETTINGS.voice_max_tokens if is_voice else SETTINGS.text_max_tokens)
    )
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
    max_tokens = SETTINGS.thinking_max_tokens if thinking_active else SETTINGS.stream_max_tokens
    sampling = get_sampling_params()
    token_gen = query_local_llm_stream(
        messages,
        system_prompt="",
        max_tokens=max_tokens,
        interruption_event=interruption_event,
        temperature=sampling["temperature"],
        min_p=sampling["min_p"],
        repeat_penalty=sampling["repeat_penalty"],
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


# ── Backward-Compatibility Forwarders ──────────────────────────

def parse_user_intent_fast(query: str):
    from amigo.utils.tool_registry import parse_user_intent_fast as _fn

    return _fn(query)


def get_agent_actions(query: str, conversation_history: list | None = None):
    from amigo.utils.tool_registry import get_agent_actions as _fn

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
    from amigo.ui.server import initialize_amigo_async, launch_server

    _info = get_active_model_info()
    port = _env_int("AMIGO_SERVER_PORT", 5000)
    print("==================================================")
    print("   AMIGO VOICE ASSISTANT - UI DASHBOARD SERVER   ")
    print(f"   [ {_info['name'].upper()} | {_info.get('tts_engine', 'POCKET TTS (KYUTAI 24KHZ)').upper()} | RAG + AGENTIC ]")
    print("==================================================")
    initialize_amigo_async()
    launch_server(port=port, open_browser=True)