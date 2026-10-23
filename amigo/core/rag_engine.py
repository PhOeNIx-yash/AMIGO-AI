"""
Streamlined, High-Precision RAG & Working Memory Engine for Amigo Voice Assistant.
Key Architectural Fixes:
1. Information-Theoretic (IDF + Entropy) salient term extraction (zero hardcoded word lists).
2. Calibrated Hybrid Fusion (Cosine + IDF-weighted lexical overlap) preventing false-positive matches.
3. Strict Anti-Pollution Context Gatekeeping so RAG never contaminates unrelated LLM answers.
4. Overlap-preserving sentence window chunking + CSV/table structure preservation.
5. Sub-20ms cached query embeddings & parallel multi-collection retrieval.
"""

import atexit
import collections
import csv
import datetime
import hashlib
import io
import json
import logging
import math
import os
import pickle
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, cast

logger = logging.getLogger("amigo.rag_engine_v2")

for _log_name in (
    "httpx", "httpcore", "sentence_transformers", "transformers", "huggingface_hub",
    "urllib3", "chromadb", "pdfminer", "pdfminer.pdffont", "pdfminer.pdfinterp",
    "pdfminer.pdfpage", "pdfminer.pdfdocument", "pypdf", "pdfplumber",
):
    logging.getLogger(_log_name).setLevel(logging.ERROR)


class RAGEngineError(Exception):
    """Custom exception for RAG engine initialization and runtime errors."""
    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


# ── Pre-compiled Regex Patterns ────────────────────────────────
_RE_PDF_NEWLINES = re.compile(r"(?<=[a-zA-Z0-9])\n(?=[a-zA-Z0-9])")
_RE_PDF_ADJACENT = re.compile(r"([A-Z0-9]{2,})([A-Z][a-z]+)")
_RE_CHUNK_WHITESPACE = re.compile(r"\s+")
_RE_CHUNK_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_RE_MARKDOWN_HEADERS = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_RE_WORDS = re.compile(r"\b[a-zA-Z0-9_'-]{2,}\b")

# ── Paths & Constants ──────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))

RAG_DATA_DIR = os.path.join(_PROJECT_ROOT, "rag_data")
CHROMA_DIR = os.path.join(RAG_DATA_DIR, "chroma")
os.makedirs(CHROMA_DIR, exist_ok=True)

PROFILE_FILE = os.path.join(_PROJECT_ROOT, "amigo_profile.json")
BM25_INDEX_PATH = os.path.join(RAG_DATA_DIR, "bm25_index.pkl")

CONVERSATIONS = "conversations"
USER_FACTS = "user_facts"
DOCUMENTS = "documents"
EMAILS = "emails"
CALENDAR = "calendar"
ALL_COLLECTIONS = [CONVERSATIONS, USER_FACTS, DOCUMENTS, EMAILS, CALENDAR]

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".xlsx", ".csv", ".pptx"}

SKIP_RAG_TOOLS = {
    "play_youtube", "pause", "resume", "stop", "volume", "mute", "unmute",
    "open", "close", "launch", "scroll", "click", "type",
    "set_timer", "set_reminder", "cancel_timer", "cancel_reminder",
    "get_weather", "get_time", "get_date",
    "system_volume", "system_brightness", "system_power",
}

LOW_VALUE_TOOLS = {
    "volume", "mute", "unmute", "volume_up", "volume_down",
    "system_volume", "system_brightness", "system_power",
    "scroll", "click", "type", "press_key", "click_screen",
    "pause_media", "play_media", "next_track", "prev_track",
    "stop", "stop_speaking",
}

MAX_VOICE_CONTEXT_CHARS = 1200
MAX_FILE_SIZE = 50 * 1024 * 1024
CHUNK_SIZE = 600
CHUNK_OVERLAP = 150
EMBEDDING_MODEL = os.getenv(
    "AMIGO_EMBEDDING_MODEL",
    "nomic-ai/nomic-embed-text-v1.5",
)

STATE_TTLS = {
    "current_media": 1800,
    "active_app": 1800,
    "last_search": 900,
    "active_subject": 600,
    "active_file": 1800,
}

# ── Runtime State & Locks ──────────────────────────────────────
_chroma_client = None
_embedding_fn = None
_collections: dict[str, Any] = {}
_init_lock = threading.Lock()
_initialized = False
_init_failed = False
_init_error: Exception | None = None

_bm25_index = None
_bm25_doc_map: dict[int, dict] = {}
_bm25_lock = threading.Lock()
_bm25_rebuild_timer: threading.Timer | None = None
_bm25_timer_lock = threading.Lock()

# Online token frequency tracker for dynamic IDF estimation when BM25 is empty or warming up
_token_doc_freq: collections.Counter = collections.Counter()
_total_tracked_chunks: int = 1

_profile_cache: dict | None = None
_profile_mtime: float = 0.0
_profile_last_check: float = 0.0
_profile_lock = threading.Lock()

_BUFFER_MAX = 15
_conversation_buffer: collections.deque | None = None
_all_conversations_cache: collections.deque | None = None
_conv_cache_lock = threading.Lock()

_SEARCH_CACHE: collections.OrderedDict[str, tuple[float, list[dict]]] = collections.OrderedDict()
_SEARCH_CACHE_TTL = 60.0
_SEARCH_CACHE_LOCK = threading.Lock()

_extracted_text_cache: collections.OrderedDict[tuple[str, float], str] = collections.OrderedDict()
_extracted_text_lock = threading.Lock()

_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="AmigoRAG")


@dataclass
class SearchResult:
    text: str
    source: str
    score: float
    metadata: dict = field(default_factory=dict)
    rerank_score: float = 0.0
    keyword_score: float = 0.0
    lexical_overlap: float = 0.0
    raw_semantic: float = 0.0
    importance: float = 1.0


# ═══════════════════════════════════════════════════════════════
#  1. Fast Cached Embeddings & Chroma Initialization
# ═══════════════════════════════════════════════════════════════

class NomicEmbeddingFunction:
    """Nomic Embed Text v1.5 (768-d) embedding function with 2048 token context & LRU query caching."""
    is_legacy: bool = False

    def __init__(self, target_model: str = EMBEDDING_MODEL):
        import torch
        from transformers import AutoTokenizer, AutoModel, PreTrainedModel

        if not hasattr(PreTrainedModel, "get_extended_attention_mask"):
            def _get_extended_attention_mask(self, attention_mask, input_shape=None, dtype=None):
                if dtype is None:
                    dtype = getattr(self, "dtype", torch.float32)
                if attention_mask.dim() == 3:
                    extended = attention_mask[:, None, :, :]
                elif attention_mask.dim() == 2:
                    extended = attention_mask[:, None, None, :]
                else:
                    extended = attention_mask
                extended = extended.to(dtype=dtype)
                return (1.0 - extended) * -10000.0

            setattr(PreTrainedModel, "get_extended_attention_mask", _get_extended_attention_mask)

        self.target_model = target_model
        self.tokenizer = AutoTokenizer.from_pretrained(target_model)
        self.model = AutoModel.from_pretrained(target_model, trust_remote_code=True)
        self.model.eval()
        self._lock = threading.Lock()
        self._query_cache: collections.OrderedDict[str, list[float]] = collections.OrderedDict()

    def name(self) -> str:
        return "nomic_embed_text_v1_5"

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine"]

    def embed_with_retries(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def get_config(self) -> dict[str, str]:
        return {"target_model": self.target_model}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "NomicEmbeddingFunction":
        return NomicEmbeddingFunction(str(config["target_model"]))

    def validate_config_update(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> None:
        if old_config != new_config:
            raise ValueError("Nomic embedding configuration is immutable")

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        if not isinstance(config.get("target_model"), str):
            raise ValueError("Embedding configuration must include a target_model")

    def _mean_pooling(self, model_output, attention_mask):
        import torch
        token_embeddings = model_output[0]
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)

    def __call__(self, input: list[str] | str) -> list[list[float]]:
        import torch
        import torch.nn.functional as F

        if isinstance(input, str):
            input = [input]
        formatted = [
            t if t.startswith(("search_document: ", "search_query: ")) else f"search_document: {t}"
            for t in input
        ]
        with self._lock, torch.no_grad():
            encoded = self.tokenizer(
                formatted,
                padding=True,
                truncation=True,
                max_length=2048,
                return_tensors="pt",
            )
            model_out = self.model(**encoded)
            pooled = self._mean_pooling(model_out, encoded["attention_mask"])
            normalized = F.normalize(pooled, p=2, dim=1)
            return normalized.tolist()

    def embed_query(self, input: list[str] | str) -> list[list[float]]:
        """Embed queries with search_query: prefix and thread-safe LRU caching."""
        import torch
        import torch.nn.functional as F

        if isinstance(input, str):
            input = [input]

        results: list[list[float] | None] = [None] * len(input)
        missing_indices: list[int] = []
        missing_prompts: list[str] = []

        with self._lock:
            for i, text in enumerate(input):
                clean_text = text.strip()
                key = clean_text.lower()
                if key in self._query_cache:
                    self._query_cache.move_to_end(key)
                    results[i] = self._query_cache[key]
                else:
                    prompt = clean_text if clean_text.startswith("search_query: ") else f"search_query: {clean_text}"
                    missing_indices.append(i)
                    missing_prompts.append(prompt)

            if missing_prompts:
                with torch.no_grad():
                    encoded = self.tokenizer(
                        missing_prompts,
                        padding=True,
                        truncation=True,
                        max_length=2048,
                        return_tensors="pt",
                    )
                    model_out = self.model(**encoded)
                    pooled = self._mean_pooling(model_out, encoded["attention_mask"])
                    normalized = F.normalize(pooled, p=2, dim=1).tolist()

                for idx, emb in zip(missing_indices, normalized):
                    results[idx] = emb
                    key = input[idx].strip().lower()
                    self._query_cache[key] = emb
                    if len(self._query_cache) > 256:
                        self._query_cache.popitem(last=False)

        return [r for r in results if r is not None]


def _init_chroma() -> None:
    global _chroma_client, _embedding_fn, _collections, _initialized, _init_failed
    with _init_lock:
        if _chroma_client is not None:
            return
        try:
            import chromadb
            client = chromadb.PersistentClient(path=CHROMA_DIR)
            _embedding_fn = NomicEmbeddingFunction(EMBEDDING_MODEL)
            _recreate_collections_if_needed(client, _embedding_fn)

            from chromadb.api.types import EmbeddingFunction

            for name in ALL_COLLECTIONS:
                _collections[name] = client.get_or_create_collection(
                    name=name,
                    embedding_function=cast(EmbeddingFunction, _embedding_fn),
                    metadata={"hnsw:space": "cosine", "embedding_model": _embedding_fn.name()},
                )

            _chroma_client = client
            _init_bm25_index()
            _initialized = True
            _init_failed = False
            logger.info("[RAG v2] Ready with %d collections", len(_collections))
        except Exception as e:
            _init_failed = True
            _initialized = False
            logger.error("[RAG v2] Init failed: %s", e)
            raise


def _recreate_collections_if_needed(client, embedding_fn) -> None:
    try:
        test_emb = embedding_fn(["test"])
        expected_dim = len(test_emb[0]) if test_emb else 768
        target_model_name = getattr(embedding_fn, "name", lambda: "nomic_embed_text_v1_5")()
        saved_facts = None

        for name in ALL_COLLECTIONS:
            try:
                col = client.get_collection(name=name)
                col_meta = col.metadata or {}
                model_mismatch = col_meta.get("embedding_model") != target_model_name

                dim_mismatch = False
                if col and col.count() > 0:
                    sample = col.get(limit=1, include=["embeddings"])
                    embs = sample.get("embeddings") if sample else None
                    if embs is not None and len(embs) > 0 and len(embs[0]) != expected_dim:
                        dim_mismatch = True

                if dim_mismatch or model_mismatch:
                    if name == USER_FACTS and col.count() > 0:
                        saved_facts = col.get(include=["documents", "metadatas"])
                    client.delete_collection(name=name)
                    logger.info("[RAG v2] Recreating collection '%s' for embedding model: %s", name, target_model_name)
            except Exception:
                pass

        if saved_facts and saved_facts.get("documents"):
            col = client.get_or_create_collection(
                name=USER_FACTS,
                embedding_function=embedding_fn,
                metadata={"hnsw:space": "cosine", "embedding_model": target_model_name},
            )
            col.add(ids=saved_facts["ids"], documents=saved_facts["documents"], metadatas=saved_facts["metadatas"])
    except Exception as e:
        logger.debug("[RAG v2] Dimension/model check note: %s", e)


def _col(name: str):
    if _chroma_client is None:
        if _init_failed:
            raise RAGEngineError("RAG engine initialization failed.")
        _init_chroma()
    return _collections.get(name)


def is_rag_ready() -> bool:
    return _initialized and _chroma_client is not None and not _init_failed


# ═══════════════════════════════════════════════════════════════
#  2. Fast BM25 Keyword Index & Dynamic Token IDF
# ═══════════════════════════════════════════════════════════════

def _init_bm25_index() -> None:
    global _bm25_index, _bm25_doc_map
    try:
        if os.path.exists(BM25_INDEX_PATH):
            with open(BM25_INDEX_PATH, "rb") as f:
                data = pickle.load(f)
                _bm25_index = data.get("index")
                _bm25_doc_map = data.get("doc_map", {})
        if (
            (_bm25_index is None or not _bm25_doc_map)
            and _collections.get(DOCUMENTS)
            and _collections[DOCUMENTS].count() > 0
        ):
            _executor.submit(_rebuild_bm25_index)
    except Exception as e:
        logger.warning("[RAG v2] BM25 load failed: %s", e)
        _bm25_index, _bm25_doc_map = None, {}


def _rebuild_bm25_index() -> None:
    global _bm25_index, _bm25_doc_map, _token_doc_freq, _total_tracked_chunks
    with _bm25_lock:
        try:
            from rank_bm25 import BM25Okapi
            col = _col(DOCUMENTS)
            if not col or col.count() == 0:
                _bm25_index, _bm25_doc_map = None, {}
                return

            results = col.get(include=["documents", "metadatas"])
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []
            ids = results.get("ids") or []
            if not docs:
                return

            corpus = []
            df_counter: collections.Counter = collections.Counter()
            for d in docs:
                tokens = _RE_WORDS.findall(d.lower())
                corpus.append(tokens)
                df_counter.update(set(tokens))

            # Store full chunk text (up to 1500 chars) so BM25 matches never truncate answers
            doc_map = {
                i: {"text": d[:1500], "metadata": m, "id": ids[i] if i < len(ids) else str(i)}
                for i, (d, m) in enumerate(zip(docs, metas))
            }
            new_index = BM25Okapi(corpus)
            _bm25_doc_map = doc_map
            _bm25_index = new_index
            _token_doc_freq = df_counter
            _total_tracked_chunks = max(1, len(docs))

            with open(BM25_INDEX_PATH, "wb") as f:
                pickle.dump({"index": new_index, "doc_map": doc_map}, f)
        except Exception as e:
            logger.error("[RAG v2] BM25 rebuild failed: %s", e)


def _schedule_bm25_rebuild(delay: float = 3.0) -> None:
    """Debounce BM25 rebuilds to prevent disk/CPU thrashing during rapid consecutive indexing."""
    global _bm25_rebuild_timer
    with _bm25_timer_lock:
        if _bm25_rebuild_timer is not None:
            _bm25_rebuild_timer.cancel()
        _bm25_rebuild_timer = threading.Timer(delay, lambda: _executor.submit(_rebuild_bm25_index))
        _bm25_rebuild_timer.daemon = True
        _bm25_rebuild_timer.start()


def _token_information_weight(token: str) -> float:
    """
    Compute the information weight (specificity) of a token dynamically without hardcoded stopword lists.
    Combines:
    1. Live corpus Inverse Document Frequency (IDF) from BM25 / tracked chunks when available.
    2. Morphological specificity (sub-linear character length + digit/mixed-case indicators).
    """
    low = token.lower()
    if len(low) < 2:
        return 0.0

    # Morphological specificity: short functional particles (2-3 chars) naturally carry low weight
    # unless they contain digits or uppercase acronyms.
    has_digit = any(c.isdigit() for c in token)
    is_acronym = len(token) >= 2 and token.isupper()
    length_factor = min(1.0, max(0.15, (len(low) - 2.0) / 5.0))
    if has_digit or is_acronym:
        length_factor = max(length_factor, 0.85)

    # Corpus IDF factor if BM25 index is populated
    bm25 = _bm25_index
    if bm25 is not None and hasattr(bm25, "idf") and isinstance(bm25.idf, dict):
        idf_val = bm25.idf.get(low)
        if idf_val is not None:
            # Normalize positive BM25 IDF into [0.1, 1.0]; common words across all chunks have near-0 or negative IDF
            idf_norm = min(1.0, max(0.1, float(idf_val) / 5.0))
            return round(0.6 * idf_norm + 0.4 * length_factor, 4)

    if _total_tracked_chunks > 5 and low in _token_doc_freq:
        df = _token_doc_freq[low]
        idf_est = math.log(1.0 + (_total_tracked_chunks - df + 0.5) / (df + 0.5))
        idf_norm = min(1.0, max(0.1, idf_est / 4.0))
        return round(0.6 * idf_norm + 0.4 * length_factor, 4)

    return round(length_factor, 4)


def _compute_lexical_overlap(query_tokens: list[str], doc_text: str) -> float:
    """
    Compute information-weighted lexical overlap in [0.0, 1.0] between query tokens and candidate chunk.
    Prevents high-frequency functional words from falsely validating an irrelevant chunk.
    """
    if not query_tokens or not doc_text:
        return 0.0

    doc_tokens = set(_RE_WORDS.findall(doc_text.lower()))
    if not doc_tokens:
        return 0.0

    total_weight = 0.0
    matched_weight = 0.0
    for qt in query_tokens:
        low_q = qt.lower()
        w = _token_information_weight(qt)
        if w < 0.25:
            continue
        total_weight += w
        if low_q in doc_tokens:
            matched_weight += w
        elif len(low_q) >= 5:
            # Prefix stem match (e.g., "indexing" <-> "indexed") without hardcoded stemmers
            stem = low_q[: max(4, len(low_q) - 2)]
            if any(dt.startswith(stem) for dt in doc_tokens if len(dt) >= 4):
                matched_weight += w * 0.75

    if total_weight <= 1e-6:
        return 0.0
    return round(min(1.0, matched_weight / total_weight), 4)


# ═══════════════════════════════════════════════════════════════
#  3. Profile & Active Environment State
# ═══════════════════════════════════════════════════════════════

def _default_profile() -> dict:
    return {
        "identity": {"name": "", "role": ""},
        "ui_settings": {},
        "preferences": {},
        "stats": {"total_interactions": 0, "last_active": None},
        "recent_conversations": [],
    }


def load_profile() -> dict:
    global _profile_cache, _profile_mtime, _profile_last_check
    now = time.time()
    if _profile_cache is not None and (now - _profile_last_check < 5.0):
        return _profile_cache
    _profile_last_check = now

    current_mtime = os.path.getmtime(PROFILE_FILE) if os.path.exists(PROFILE_FILE) else 0.0
    if _profile_cache is not None and current_mtime <= _profile_mtime:
        return _profile_cache

    with _profile_lock:
        if os.path.exists(PROFILE_FILE):
            try:
                with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    default = _default_profile()
                    for k, v in default.items():
                        loaded.setdefault(k, v)
                    _profile_cache = loaded
                    _profile_mtime = current_mtime
                    return _profile_cache
            except Exception:
                pass
        _profile_cache = _default_profile()
        _profile_mtime = current_mtime
        return _profile_cache


def _persist_profile_sync(profile_snapshot: dict) -> None:
    global _profile_mtime
    with _profile_lock:
        tmp = PROFILE_FILE + ".tmp"
        for attempt in range(3):
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(profile_snapshot, f, indent=2)
                os.replace(tmp, PROFILE_FILE)
                _profile_mtime = os.path.getmtime(PROFILE_FILE)
                return
            except (PermissionError, OSError) as pe:
                time.sleep(0.05)
                try:
                    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                        json.dump(profile_snapshot, f, indent=2)
                    _profile_mtime = os.path.getmtime(PROFILE_FILE)
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
                    return
                except Exception:
                    if attempt == 2:
                        logger.warning("[RAG v2] Profile save fallback failed: %s", pe)
            except Exception as e:
                if attempt == 2:
                    logger.error("[RAG v2] Profile save error: %s", e)
                time.sleep(0.05)


def save_profile(profile: dict) -> None:
    global _profile_cache
    _profile_cache = profile
    _executor.submit(_persist_profile_sync, dict(profile))


_ACTIVE_STATE: dict[str, Any] = {
    "current_media": None,
    "active_app": {"name": "", "timestamp": None},
    "last_search": {"query": "", "timestamp": None},
    "active_subject": {"name": "", "category": "", "timestamp": None},
    "active_file": {"path": "", "name": "", "timestamp": None},
}


def _is_expired(ts_str: str | None, ttl: int) -> bool:
    if not ts_str:
        return True
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(ts_str)).total_seconds() > ttl
    except Exception:
        return True


def get_active_state(clean_expired: bool = True) -> dict:
    if clean_expired:
        for slot, ttl in STATE_TTLS.items():
            val = _ACTIVE_STATE.get(slot)
            if isinstance(val, dict) and _is_expired(val.get("timestamp"), ttl):
                if slot == "current_media":
                    _ACTIVE_STATE[slot] = None
                else:
                    _ACTIVE_STATE[slot] = {k: (None if k == "timestamp" else "") for k in val}
    return _ACTIVE_STATE


def update_active_state(slot: str, data: dict | list | None) -> None:
    """Update or reset an active-state slot in memory."""
    if not slot:
        return
    if data is None:
        if slot == "current_media":
            _ACTIVE_STATE[slot] = None
        else:
            existing = _ACTIVE_STATE.get(slot)
            if isinstance(existing, dict):
                _ACTIVE_STATE[slot] = {k: (None if k == "timestamp" else "") for k in existing}
            else:
                _ACTIVE_STATE[slot] = None
        return

    slot_data: dict[str, Any] = (
        {"items": data}
        if isinstance(data, list)
        else (dict(data) if isinstance(data, dict) else {})
    )
    if not slot_data:
        return
    slot_data["timestamp"] = datetime.datetime.now().isoformat()
    _ACTIVE_STATE[slot] = slot_data


# ═══════════════════════════════════════════════════════════════
#  4. Context-Aware Follow-Up Resolution (Information-Theoretic)
# ═══════════════════════════════════════════════════════════════

def _extract_salient_terms(text: str, max_terms: int = 5) -> list[str]:
    """
    Rank and extract the highest-information content tokens from text dynamically
    using corpus IDF, token length, and casing—without hardcoded stopword lists.
    """
    if not text:
        return []
    tokens = _RE_WORDS.findall(text)
    if not tokens:
        return []

    scored_candidates: list[tuple[float, int, str]] = []
    seen: set[str] = set()

    for idx, t in enumerate(tokens):
        low = t.lower()
        if len(low) < 3 or low.isdigit() or low in seen:
            continue
        seen.add(low)

        weight = _token_information_weight(t)
        # Boost mid-sentence capitalized proper nouns / technical identifiers
        if idx > 0 and t[0].isupper() and not t.isupper():
            weight += 0.25
        elif t.isupper() and len(t) >= 2:
            weight += 0.30

        # Only retain tokens with meaningful information density
        if weight >= 0.45:
            scored_candidates.append((weight, idx, t))

    # Sort by information weight descending, preserving original order on ties
    scored_candidates.sort(key=lambda x: (-x[0], x[1]))
    top_selected = sorted(scored_candidates[:max_terms], key=lambda x: x[1])
    return [item[2] for item in top_selected]


def resolve_contextual_query(query: str, conversation_history: list[dict] | None = None) -> str:
    """
    Dynamically resolves follow-up questions of ANY length by checking whether the query
    already explicitly mentions the active entity from recent conversation.
    Zero arbitrary word-count limits.
    """
    if not query or not query.strip():
        return ""

    clean_q = query.strip()
    history = conversation_history if conversation_history is not None else get_recent_conversations(count=2)
    if not history:
        return clean_q

    last_turn = history[-1]
    ts_str = last_turn.get("timestamp")
    # Only inherit conversational context if the previous turn was recent (< 5 minutes old)
    if ts_str and _is_expired(ts_str, 300):
        return clean_q

    u_msg = last_turn.get("user", "")
    a_msg = last_turn.get("assistant", "")

    # Extract salient anchors from the recent turn using information weights
    context_anchors: list[str] = _extract_salient_terms(u_msg, max_terms=5)

    if not context_anchors:
        return clean_q

    # Check if the query already explicitly mentions any of the context entities
    q_words = {w.lower() for w in _RE_WORDS.findall(clean_q)}
    already_mentions_entity = any(term.lower() in q_words for term in context_anchors)
    if already_mentions_entity:
        return clean_q

    # Query does not mention the active entity: attach anchors naturally
    unique_anchors: list[str] = []
    for term in context_anchors:
        if term.lower() not in q_words and term.lower() not in {a.lower() for a in unique_anchors}:
            unique_anchors.append(term)
        if len(unique_anchors) >= 4:
            break

    if not unique_anchors:
        return clean_q

    resolved = f"{clean_q} {' '.join(unique_anchors)}"
    logger.debug("[RAG v2] Resolved follow-up query: '%s' -> '%s'", clean_q, resolved)
    return resolved


def expand_query(query: str, conversation_history: list | None = None) -> list[str]:
    """Return [resolved_contextual_query, original_query] if context enriches the query."""
    if not query or not query.strip():
        return [query]
    orig = query.strip()
    resolved = resolve_contextual_query(orig, conversation_history)
    if resolved != orig:
        return [resolved, orig]
    return [orig]


# ═══════════════════════════════════════════════════════════════
#  5. Parallel Hybrid Search & Calibrated Relevance Scoring
# ═══════════════════════════════════════════════════════════════

def clear_search_cache() -> None:
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE.clear()


def _is_conversational_filler(query: str) -> bool:
    if not query or not query.strip():
        return True
    tokens = _RE_WORDS.findall(query)
    if not tokens:
        return True
    if len(tokens) == 1 and len(tokens[0]) <= 3 and not any(ch in query for ch in ("?", "!", "$", "%")):
        return True
    return False


def _score_and_rank(
    candidates: list[SearchResult],
    query: str,
    top_k: int,
) -> list[SearchResult]:
    """
    Calibrated Hybrid Scoring Pipeline:
    1. Deduplicates chunks across vector & BM25 retrievers.
    2. Computes information-weighted lexical overlap with the query.
    3. Applies non-linear cosine calibration (suppresses Nomic's 0.45-0.58 baseline noise floor).
    4. Prevents ungrounded vector matches or isolated single-word BM25 hits from ranking high.
    """
    query_tokens = _RE_WORDS.findall(query)
    deduped: dict[str, SearchResult] = {}

    for r in candidates:
        norm_prefix = _RE_CHUNK_WHITESPACE.sub(" ", r.text.strip()[:200]).lower()
        key = f"{r.source}::{norm_prefix}"
        if key not in deduped:
            deduped[key] = r
        else:
            existing = deduped[key]
            existing.score = max(existing.score, r.score)
            existing.raw_semantic = max(existing.raw_semantic, r.raw_semantic)
            existing.keyword_score = max(existing.keyword_score, r.keyword_score)

    now_dt = datetime.datetime.now()
    scored_results: list[SearchResult] = []

    for r in deduped.values():
        sem_raw = max(0.0, min(1.0, r.raw_semantic if r.raw_semantic > 0 else r.score))
        r.raw_semantic = sem_raw

        lex_overlap = _compute_lexical_overlap(query_tokens, r.text)
        r.lexical_overlap = lex_overlap

        # Calibrate Nomic cosine similarity: map [0.35, 0.90] -> [0.0, 1.0]
        sem_calibrated = max(0.0, min(1.0, (sem_raw - 0.35) / 0.50))

        # Normalize BM25 score with saturation
        kw_norm = min(1.0, r.keyword_score / 15.0) if r.keyword_score > 0 else 0.0

        if sem_raw > 0 and kw_norm > 0:
            # True hybrid hit (found by both dense vector and BM25)
            combined = 0.55 * sem_calibrated + 0.30 * kw_norm + 0.15 * lex_overlap
        elif sem_raw > 0:
            if lex_overlap > 0.08:
                combined = 0.70 * sem_calibrated + 0.30 * lex_overlap
            else:
                combined = sem_calibrated * 0.85
        else:
            # BM25-only hit
            combined = 0.50 * kw_norm + 0.50 * lex_overlap

        # Recency decay for episodic conversations (half-life = 14 days)
        recency_mult = 1.0
        if r.source == CONVERSATIONS:
            ts_str = r.metadata.get("timestamp") if isinstance(r.metadata, dict) else None
            if ts_str:
                try:
                    age_days = max(0.0, (now_dt - datetime.datetime.fromisoformat(ts_str)).total_seconds() / 86400.0)
                    recency_mult = 0.82 + 0.18 * (0.5 ** (age_days / 14.0))
                except Exception:
                    pass

        imp_mult = 1.0 + 0.08 * ((r.importance or 1.0) - 0.5)
        r.score = round(min(1.0, max(0.0, combined * imp_mult * recency_mult)), 4)
        scored_results.append(r)

    scored_results.sort(key=lambda x: x.score, reverse=True)
    return scored_results[:top_k]


def _query_single_collection(
    col_name: str,
    query_embeddings: list[list[float]],
    n_results: int,
    metadata_filter: dict | None,
) -> list[SearchResult]:
    """Query a single ChromaDB collection using precomputed embeddings."""
    if not query_embeddings:
        return []
    try:
        col = _col(col_name)
        if not col:
            return []
        count = col.count()
        if count == 0:
            return []

        kwargs: dict[str, Any] = {
            "query_embeddings": query_embeddings,
            "n_results": min(n_results, count),
            "include": ["documents", "metadatas", "distances"],
        }
        if metadata_filter:
            kwargs["where"] = metadata_filter

        res = col.query(**kwargs)
        out: list[SearchResult] = []
        if res and res.get("documents"):
            # Discount fallback/expanded queries slightly vs primary query
            for q_idx, (docs, metas, dists) in enumerate(zip(res["documents"], res["metadatas"], res["distances"])):
                weight = 1.0 if q_idx == 0 else 0.94
                for doc, meta, dist in zip(docs, metas, dists):
                    imp = float(meta.get("importance", 1.0)) if isinstance(meta, dict) else 1.0
                    raw_sem = round((1.0 - dist) * weight, 4)
                    out.append(SearchResult(
                        text=doc,
                        source=col_name,
                        score=raw_sem,
                        raw_semantic=raw_sem,
                        metadata=meta or {},
                        importance=imp,
                    ))
        return out
    except Exception as e:
        logger.debug("[RAG v2] Error querying %s: %s", col_name, e)
        return []


def _bm25_search(query: str, top_k: int, metadata_filter: dict | None = None) -> list[SearchResult]:
    bm25 = _bm25_index
    doc_map = _bm25_doc_map
    if bm25 is None or not doc_map:
        return []
    try:
        # Filter out low-information particles before querying BM25
        raw_tokens = _RE_WORDS.findall(query.lower())
        tokens = [t for t in raw_tokens if len(t) >= 2 and not t.isdigit() and _token_information_weight(t) >= 0.30]
        if not tokens:
            return []

        scores = bm25.get_scores(tokens)
        non_zero = [(idx, float(s)) for idx, s in enumerate(scores) if s > 1.5]
        non_zero.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, raw_score in non_zero[: top_k * 2]:
            doc_info = doc_map.get(idx)
            if not doc_info:
                continue
            meta = doc_info["metadata"]
            if metadata_filter and any(meta.get(k) != v for k, v in metadata_filter.items()):
                continue
            results.append(SearchResult(
                text=doc_info["text"],
                source=DOCUMENTS,
                score=min(1.0, raw_score / 20.0),
                raw_semantic=0.0,
                metadata=meta,
                keyword_score=raw_score,
            ))
            if len(results) >= top_k:
                break
        return results
    except Exception as e:
        logger.debug("[RAG v2] BM25 search error: %s", e)
        return []


def search(
    query: str,
    target_collections: list[str] | None = None,
    top_k: int = 5,
    query_type: str = "auto",
    metadata_filter: dict | None = None,
    conversation_history: list | None = None,
    skip_rag: bool = False,
    **kwargs,
) -> list[dict]:
    """
    Unified, low-latency hybrid search:
    - Resolves follow-up context conservatively.
    - Computes query embeddings once (cached in RAM).
    - Queries target ChromaDB collections + BM25 concurrently.
    - Scores and calibrates results using semantic + information-weighted lexical overlap.
    """
    if skip_rag or not query or not query.strip() or _is_conversational_filler(query):
        return []

    if target_collections is None:
        if query_type in ("conversation_recall", "memory", "fact_lookup"):
            target_collections = [USER_FACTS, CONVERSATIONS]
        elif query_type in ("document_qa", "document", "file"):
            target_collections = [DOCUMENTS]
        else:
            target_collections = [DOCUMENTS, USER_FACTS, CONVERSATIONS]

    if not target_collections:
        return []

    queries = expand_query(query, conversation_history)
    effective_query_key = "|".join(queries)

    cache_key = f"{effective_query_key.lower()}::{','.join(sorted(target_collections))}::{top_k}::{json.dumps(metadata_filter or {}, sort_keys=True)}"
    now = time.time()
    with _SEARCH_CACHE_LOCK:
        if cache_key in _SEARCH_CACHE:
            ts, cached_res = _SEARCH_CACHE[cache_key]
            if now - ts < _SEARCH_CACHE_TTL:
                _SEARCH_CACHE.move_to_end(cache_key)
                return [dict(r) for r in cached_res]

    _col(target_collections[0])
    query_embeddings = _embedding_fn.embed_query(queries) if _embedding_fn else []
    pool_size = max(top_k * 2, 10)

    search_futures = [
        _executor.submit(_query_single_collection, c, query_embeddings, pool_size, metadata_filter)
        for c in target_collections
    ]
    if DOCUMENTS in target_collections:
        search_futures.append(_executor.submit(_bm25_search, queries[0], pool_size, metadata_filter))

    candidates: list[SearchResult] = []
    for f in search_futures:
        try:
            candidates.extend(f.result(timeout=2.0))
        except Exception as e:
            logger.debug("[RAG v2] Parallel retrieval worker error: %s", e)

    ranked = _score_and_rank(candidates, queries[0], top_k)
    dict_results = [
        {
            "text": r.text,
            "source": r.source,
            "score": r.score,
            "raw_semantic": r.raw_semantic,
            "lexical_overlap": r.lexical_overlap,
            "metadata": r.metadata,
            "rerank_score": r.rerank_score,
            "keyword_score": r.keyword_score,
        }
        for r in ranked
    ]

    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[cache_key] = (now, dict_results)
        if len(_SEARCH_CACHE) > 512:
            _SEARCH_CACHE.popitem(last=False)

    return dict_results


def build_document_evidence(
    query: str,
    top_k: int = 6,
) -> tuple[str, list[dict[str, Any]]]:
    """Build answer context and citations only from sufficiently relevant document passages."""
    results = search(
        query,
        target_collections=[DOCUMENTS],
        top_k=top_k,
        query_type="document_qa",
    )
    evidence_blocks: list[str] = []
    sources: list[dict[str, Any]] = []

    for result in results:
        score = result.get("score")
        text = result.get("text")
        metadata = result.get("metadata")
        if (
            result.get("source") != DOCUMENTS
            or not isinstance(score, (int, float))
            or score < 0.2
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(metadata, dict)
        ):
            continue

        filepath = metadata.get("filepath")
        filename = metadata.get("filename")
        if not isinstance(filename, str) or not filename:
            filename = os.path.basename(filepath) if isinstance(filepath, str) else "Unknown file"
        chunk_index = metadata.get("chunk_index")
        location = f"passage {chunk_index + 1}" if isinstance(chunk_index, int) else "document"
        source_id = len(sources) + 1
        sources.append({"id": source_id, "file": filename, "location": location})
        evidence_blocks.append(f"[Source {source_id}: {filename}, {location}]\n{text.strip()}")

    return "\n\n".join(evidence_blocks), sources


def should_skip_rag_for_tool(tool: str) -> bool:
    return tool in SKIP_RAG_TOOLS


def search_documents(query: str, top_k: int = 5) -> list[dict]:
    return search(query, target_collections=[DOCUMENTS], top_k=top_k)


def search_memory(query: str, top_k: int = 5) -> list[dict]:
    return search(query, target_collections=[CONVERSATIONS, USER_FACTS], top_k=top_k)


def search_emails_rag(query: str, top_k: int = 5) -> list[dict]:
    return search(query, target_collections=[EMAILS], top_k=top_k)


def search_calendar_rag(query: str, top_k: int = 5) -> list[dict]:
    return search(query, target_collections=[CALENDAR], top_k=top_k)


def search_all(query: str, top_k: int = 5) -> list[dict]:
    return search(query, target_collections=ALL_COLLECTIONS, top_k=top_k)


def search_files_by_context(query: str, top_k: int = 10) -> list[dict]:
    results = search_documents(query, top_k=top_k * 3)
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        if r.get("score", 0.0) < 0.42:
            continue
        fp = r.get("metadata", {}).get("filepath", "")
        if fp and fp not in seen:
            seen.add(fp)
            unique.append({
                "filepath": fp,
                "filename": r["metadata"].get("filename", os.path.basename(fp)),
                "file_type": r["metadata"].get("file_type", ""),
                "score": r["score"],
                "preview": r["text"][:200],
            })
        if len(unique) >= top_k:
            break
    return unique


def debug_search(query: str, target_collections: list[str] | None = None, top_k: int = 10, **kwargs) -> dict:
    t0 = time.perf_counter()
    results = search(query, target_collections=target_collections, top_k=top_k, **kwargs)
    return {
        "query": query,
        "resolved_query": resolve_contextual_query(query),
        "target_collections": target_collections or [DOCUMENTS, USER_FACTS, CONVERSATIONS],
        "top_k": top_k,
        "count": len(results),
        "results": results,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        "reranker_active": False,
        "bm25_active": _bm25_index is not None,
        "stats": get_index_stats(),
    }


def reinitialize_reranker() -> bool:
    return False


# ═══════════════════════════════════════════════════════════════
#  6. Anti-Pollution Smart Context Builder
# ═══════════════════════════════════════════════════════════════

def _is_chunk_grounded(r: dict, floor: float) -> bool:
    """
    Source-specific gatekeeper that verifies a retrieved chunk is genuinely
    grounded in the query before allowing it into the LLM prompt.
    """
    score = float(r.get("score", 0.0) or 0.0)
    raw_sem = float(r.get("raw_semantic", score) or 0.0)
    lex = float(r.get("lexical_overlap", 0.0) or 0.0)
    kw = float(r.get("keyword_score", 0.0) or 0.0)
    src = r.get("source", "")

    if score < floor:
        return False

    if src == DOCUMENTS:
        # A document chunk is grounded if it has decent semantic match or lexical/keyword match
        return (
            raw_sem >= 0.50
            or (raw_sem >= 0.42 and (lex >= 0.10 or kw >= 1.0))
            or kw >= 2.5
            or score >= 0.25
        )

    if src == CONVERSATIONS:
        # Past conversations are filtered strictly so old chat doesn't pollute new turns
        return (raw_sem >= 0.65 and lex >= 0.20) or raw_sem >= 0.75 or score >= 0.45

    if src == USER_FACTS:
        return raw_sem >= 0.48 or lex >= 0.15 or score >= 0.25

    if src in (EMAILS, CALENDAR):
        return raw_sem >= 0.48 or lex >= 0.15 or score >= 0.25

    return score >= floor


def build_rag_context(
    query: str,
    top_k: int = 5,
    conversation_history: list | None = None,
    for_voice: bool = True,
    search_results: list | None = None,
    min_score: float | None = None,
    target_collections: list[str] | None = None,
) -> str:
    """
    Build high-precision, non-polluting RAG context for LLM prompt injection:
    - Targets user documents and facts by default, avoiding conversational failure loops.
    - Filters out ungrounded semantic noise using source-specific lexical + calibrated score gates.
    """
    if not query or not query.strip() or _is_conversational_filler(query):
        return ""

    history = conversation_history if conversation_history is not None else get_recent_conversations(count=4)
    resolved_query = resolve_contextual_query(query, history)
    is_followup = resolved_query != query.strip()

    if search_results is not None:
        results = search_results
    else:
        collections_to_search = target_collections if target_collections is not None else [DOCUMENTS, USER_FACTS, EMAILS, CALENDAR]
        results = search(
            query,
            target_collections=collections_to_search,
            top_k=top_k,
            conversation_history=history,
        )

    parts: list[str] = []
    total_chars = 0
    max_chars = MAX_VOICE_CONTEXT_CHARS if for_voice else 8000

    # 1. Include compact working memory (last 2 turns) ONLY if a follow-up reference was resolved
    if is_followup and history:
        recent_turns_str = []
        for turn in history[-2:]:
            u = turn.get("user", "").strip()
            a = turn.get("assistant", "").strip()[:250]
            if u:
                recent_turns_str.append(f"User: {u}\nAssistant: {a}")
        if recent_turns_str:
            wm_block = "[Recent Conversation Context]:\n" + "\n".join(recent_turns_str)
            parts.append(wm_block)
            total_chars += len(wm_block)

    # 2. Grounding verification before adding any chunk
    state = get_active_state(clean_expired=True)
    active_fp = state.get("active_file", {}).get("path", "")
    floor = min_score if min_score is not None else 0.20

    if results:
        grounded = [r for r in results if _is_chunk_grounded(r, floor)]
        if grounded:
            top_score = grounded[0].get("score", 0.0)
            margin = max(floor, top_score * 0.70)
            relevant = [r for r in grounded if r.get("score", 0.0) >= margin]

            k_limit = min(top_k, 3) if for_voice else max(top_k, 8)
            for r in relevant[:k_limit]:
                src = r.get("source", "")
                meta = r.get("metadata", {})
                chunk_text = (r.get("text") or "").strip()[:900]
                if not chunk_text or total_chars + len(chunk_text) > max_chars:
                    continue

                if src == DOCUMENTS:
                    fname = meta.get("filename", "document")
                    fpath = meta.get("filepath", "")
                    if fpath and os.path.exists(fpath) and r.get("score", 0.0) >= 0.35:
                        update_active_state("active_file", {"path": fpath, "name": fname})
                    parts.append(f"[From document '{fname}']:\n{chunk_text}")
                elif src == CONVERSATIONS:
                    # Never inject past failure disclaimers as evidence
                    low_chunk = chunk_text.lower()
                    if not any(f in low_chunk for f in ("couldn't find", "could not find", "don't have access", "cannot access")):
                        parts.append(f"[Past conversation]:\n{chunk_text}")
                elif src == EMAILS:
                    parts.append(f"[Email]:\n{chunk_text}")
                elif src == CALENDAR:
                    parts.append(f"[Calendar Event]:\n{chunk_text}")
                elif src == USER_FACTS:
                    parts.append(f"[Known user fact]: {chunk_text}")

                total_chars += len(chunk_text)

    # 3. Fallback: if follow-up refers to an active file that wasn't caught by general search
    if (
        not any("[From document" in p for p in parts)
        and active_fp
        and os.path.exists(active_fp)
        and is_followup
    ):
        file_ctx = build_file_context(active_fp, resolved_query, max_chars=max(400, max_chars - total_chars))
        if file_ctx:
            parts.append(file_ctx)

    if not parts:
        return ""

    body = "\n\n".join(parts)
    return (
        "<supplementary_context>\n"
        "NOTE: Use the retrieved context below ONLY if it is directly relevant to answering the user's request. "
        "If it is unrelated, ignore it completely and do not mention it.\n\n"
        f"{body}\n"
        "</supplementary_context>"
    )


def build_file_context(filepath: str, question: str, max_chars: int = 2500) -> str:
    if not os.path.exists(filepath):
        return ""
    try:
        if os.path.getsize(filepath) > MAX_FILE_SIZE or os.path.getsize(filepath) == 0:
            return ""
    except OSError:
        return ""

    canonical_path = os.path.realpath(os.path.abspath(filepath))
    filename = os.path.basename(canonical_path)
    resolved_q = resolve_contextual_query(question)

    try:
        col = _col(DOCUMENTS)
        if col and col.count() > 0:
            q_embs = _embedding_fn.embed_query([resolved_q]) if _embedding_fn else None
            for where_filter in ({"filepath": canonical_path}, {"filename": filename}):
                kwargs: dict[str, Any] = {
                    "n_results": 4,
                    "where": where_filter,
                    "include": ["documents", "distances"],
                }
                if q_embs:
                    kwargs["query_embeddings"] = q_embs
                else:
                    kwargs["query_texts"] = [resolved_q]
                res = col.query(**kwargs)
                docs = (res or {}).get("documents", [[]])[0]
                dists = (res or {}).get("distances", [[]])[0]
                if docs:
                    selected, total_len = [], 0
                    for idx, d in enumerate(docs):
                        sim = 1.0 - float(dists[idx]) if idx < len(dists) else 0.0
                        if sim < 0.52:
                            continue
                        snippet = (d or "").strip()[:850]
                        if snippet and total_len + len(snippet) <= max_chars:
                            selected.append(snippet)
                            total_len += len(snippet)
                    if selected:
                        update_active_state("active_file", {"path": canonical_path, "name": filename})
                        return f"[Relevant sections from '{filename}']:\n" + "\n\n".join(selected)
    except Exception as e:
        logger.debug("[RAG v2] build_file_context error: %s", e)

    return ""


def get_file_summary_context(filepath: str) -> str:
    text = extract_text(filepath)
    if not text:
        return ""
    fname = os.path.basename(filepath)
    update_active_state("active_file", {"path": os.path.realpath(os.path.abspath(filepath)), "name": fname})
    return f"[Content from '{fname}']:\n{text[:8000]}"


# ═══════════════════════════════════════════════════════════════
#  7. Conversation Memory & User Facts
# ═══════════════════════════════════════════════════════════════

def _ensure_buffer() -> collections.deque:
    global _conversation_buffer
    if _conversation_buffer is not None:
        return _conversation_buffer
    _conversation_buffer = collections.deque(maxlen=_BUFFER_MAX)
    for item in load_profile().get("recent_conversations", [])[-_BUFFER_MAX:]:
        _conversation_buffer.append(item)
    return _conversation_buffer


def _calculate_importance(user_msg: str, assistant_msg: str, tool: str, base_importance: float) -> float:
    tool_weights = {
        "chat": 0.55, "web_search": 0.75, "document_qa": 0.85,
        "memory_recall": 0.9, "find_file": 0.65, "set_reminder": 0.8,
    }
    imp = max(base_importance * 0.5, tool_weights.get(tool, 0.55))
    total_len = len(user_msg) + len(assistant_msg)
    if total_len > 250:
        imp = min(1.0, imp + 0.15)
    if any(kw in user_msg.lower() for kw in ("remember", "note", "save", "important", "always", "never")):
        imp = min(1.0, imp + 0.3)
    return round(imp, 2)


def add_conversation(
    user_msg: str,
    assistant_msg: str,
    tool: str = "chat",
    params: dict | None = None,
    clipboard_used: bool = False,
    remember: str = "",
    state_update: dict | None = None,
    importance: float = 1.0,
    url: str | None = None,
) -> None:
    if not user_msg or not user_msg.strip():
        return

    if state_update and isinstance(state_update, dict):
        for slot, data in state_update.items():
            update_active_state(slot, data)

    if tool in LOW_VALUE_TOOLS:
        return

    user_msg = _RE_CHUNK_WHITESPACE.sub(" ", user_msg).strip()[:1000]
    assistant_msg = _RE_CHUNK_WHITESPACE.sub(" ", assistant_msg or "").strip()[:1500]
    timestamp = datetime.datetime.now().isoformat()
    importance = _calculate_importance(user_msg, assistant_msg, tool, importance)

    # Track active subject ONLY when high-information content tokens exist
    salient = _extract_salient_terms(user_msg, max_terms=3)
    if salient:
        update_active_state("active_subject", {"name": " ".join(salient), "category": tool, "url": url or ""})

    if url:
        update_active_state("last_link", {"url": url, "title": assistant_msg[:80], "tool": tool})

    turn_item = {
        "user": user_msg,
        "assistant": assistant_msg,
        "tool": tool,
        "params": params or {},
        "timestamp": timestamp,
        "importance": importance,
    }
    if url:
        turn_item["url"] = url
    buf = _ensure_buffer()
    buf.append(turn_item)

    with _conv_cache_lock:
        if _all_conversations_cache is not None:
            _all_conversations_cache.append(turn_item)

    p = load_profile()
    rc = p.setdefault("recent_conversations", [])
    rc.append(turn_item)
    p["recent_conversations"] = rc[-_BUFFER_MAX:]
    save_profile(p)

    def _bg_persist():
        try:
            # Do not index short turns or low-importance chit-chat into long-term vector storage
            if importance >= 0.65 and len(salient) >= 2:
                col = _col(CONVERSATIONS)
                if col:
                    col.add(
                        ids=[f"conv-{uuid.uuid4().hex[:12]}"],
                        documents=[f"User: {user_msg}\nAssistant: {assistant_msg}"],
                        metadatas=[{
                            "user_msg": user_msg,
                            "assistant_msg": assistant_msg,
                            "tool": tool,
                            "params": json.dumps(params or {}, ensure_ascii=False)[:200],
                            "timestamp": timestamp,
                            "type": "conversation",
                            "importance": importance,
                        }],
                    )
            extract_user_profile_updates(user_msg, remember=remember)
        except Exception as e:
            logger.debug("[RAG v2] Background conv persist note: %s", e)

    _executor.submit(_bg_persist)


def get_recent_conversations(count: int = 4) -> list[dict]:
    return list(_ensure_buffer())[-count:]


def get_all_conversations(limit: int = 200) -> list[dict]:
    global _all_conversations_cache
    with _conv_cache_lock:
        if _all_conversations_cache is not None:
            return list(_all_conversations_cache)[-limit:]

    try:
        col = _col(CONVERSATIONS)
        if col and col.count() > 0:
            results = col.get(limit=min(col.count(), limit), include=["metadatas"])
            items = [
                {
                    "user": m.get("user_msg", ""),
                    "assistant": m.get("assistant_msg", ""),
                    "tool": m.get("tool", "chat"),
                    "timestamp": m.get("timestamp", ""),
                    "importance": m.get("importance", 1.0),
                }
                for m in (results.get("metadatas") or [])
            ]
            items.sort(key=lambda x: x.get("timestamp", ""))
            with _conv_cache_lock:
                _all_conversations_cache = collections.deque(items, maxlen=200)
            return items
    except Exception:
        pass
    return list(_ensure_buffer())[-limit:]


get_recent_conversations_from_chroma = get_recent_conversations


def get_important_conversations(min_importance: float = 0.7, limit: int = 20) -> list[dict]:
    return [c for c in _ensure_buffer() if c.get("importance", 1.0) >= min_importance][-limit:]


def summarize_conversation_history(max_turns: int = 50) -> str:
    recent = list(_ensure_buffer())[-max_turns:]
    if not recent:
        return ""
    counts = collections.Counter(t.get("tool", "chat") for t in recent if t.get("tool", "chat") != "chat")
    return "Recent activity: " + "; ".join(f"{k}: {v} interactions" for k, v in counts.items()) if counts else ""


def invalidate_conversations_cache() -> None:
    global _all_conversations_cache
    with _conv_cache_lock:
        _all_conversations_cache = None


def clear_conversations() -> None:
    global _conversation_buffer, _all_conversations_cache
    _conversation_buffer = collections.deque(maxlen=_BUFFER_MAX)
    with _conv_cache_lock:
        _all_conversations_cache = collections.deque(maxlen=200)

    try:
        if _chroma_client:
            from chromadb.api.types import EmbeddingFunction

            _chroma_client.delete_collection(CONVERSATIONS)
            _collections[CONVERSATIONS] = _chroma_client.get_or_create_collection(
                name=CONVERSATIONS,
                embedding_function=cast(EmbeddingFunction, _embedding_fn) if _embedding_fn is not None else None,
                metadata={"hnsw:space": "cosine", "embedding_model": _embedding_fn.name() if _embedding_fn else ""},
            )
    except Exception:
        pass

    for slot in _ACTIVE_STATE:
        _ACTIVE_STATE[slot] = None if slot == "current_media" else {k: "" for k in _ACTIVE_STATE[slot]}
    clear_search_cache()

    profile = load_profile()
    profile["recent_conversations"] = []
    save_profile(profile)


def add_user_fact(fact: str, category: str = "general", importance: float = 1.0) -> None:
    if not fact or len(fact.strip()) < 5:
        return
    clean_fact = fact.strip()
    doc_id = f"fact-{hashlib.md5(clean_fact.lower().encode()).hexdigest()[:12]}"
    try:
        col = _col(USER_FACTS)
        if not col:
            return
        if category == "identity":
            try:
                col.delete(where={"category": "identity"})
            except Exception:
                pass
        elif col.count() > 0 and _embedding_fn:
            q_emb = _embedding_fn.embed_query([clean_fact])
            existing = col.query(query_embeddings=q_emb, n_results=1, include=["distances"])
            if existing and existing.get("ids") and existing["ids"][0]:
                if existing["distances"][0][0] <= 0.18:
                    doc_id = existing["ids"][0][0]

        col.upsert(
            ids=[doc_id],
            documents=[clean_fact],
            metadatas=[{
                "category": category,
                "timestamp": datetime.datetime.now().isoformat(),
                "type": "user_fact",
                "importance": importance,
            }],
        )
        clear_search_cache()
    except Exception as e:
        logger.error("[RAG v2] Error adding user fact: %s", e)


def get_all_user_facts(min_importance: float = 0.0) -> list[str]:
    try:
        col = _col(USER_FACTS)
        if col and col.count() > 0:
            res = col.get(limit=200, include=["documents", "metadatas"])
            return [
                doc for doc, meta in zip(res.get("documents", []), res.get("metadatas", []))
                if meta.get("importance", 1.0) >= min_importance
            ]
    except Exception:
        pass
    return []


def clear_user_facts() -> None:
    try:
        col = _col(USER_FACTS)
        if col and col.count() > 0:
            res = col.get()
            if res and res.get("ids"):
                col.delete(ids=res["ids"])
        clear_search_cache()
    except Exception as e:
        logger.error("[RAG v2] Error clearing user facts: %s", e)


# ═══════════════════════════════════════════════════════════════
#  8. Document Extraction, Chunking & Indexing
# ═══════════════════════════════════════════════════════════════

def extract_text(filepath: str) -> str:
    if not os.path.exists(filepath):
        return ""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return ""

    cache_key = None
    try:
        cache_key = (os.path.realpath(os.path.abspath(filepath)), os.path.getmtime(filepath))
        with _extracted_text_lock:
            if cache_key in _extracted_text_cache:
                _extracted_text_cache.move_to_end(cache_key)
                return _extracted_text_cache[cache_key]
    except Exception:
        pass

    extracted = ""
    try:
        if ext == ".pdf":
            extracted = _extract_pdf_enhanced(filepath)
        elif ext == ".docx":
            extracted = _extract_docx_enhanced(filepath)
        elif ext == ".xlsx":
            extracted = _extract_xlsx_enhanced(filepath)
        elif ext == ".csv":
            extracted = _extract_csv_enhanced(filepath)
        elif ext == ".pptx":
            extracted = _extract_pptx_enhanced(filepath)
        else:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                extracted = f.read(MAX_FILE_SIZE)
    except Exception as e:
        logger.debug("[RAG v2] Extraction error for %s: %s", filepath, e)

    if cache_key and extracted and len(extracted) <= 500 * 1024:
        with _extracted_text_lock:
            _extracted_text_cache[cache_key] = extracted
            if len(_extracted_text_cache) > 15:
                _extracted_text_cache.popitem(last=False)

    return extracted


def _format_table_as_markdown(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    cleaned = []
    max_cols = 0
    for r in rows:
        cells = [str(c or "").strip().replace("\n", " ").replace("|", "\\|") for c in r]
        if any(cells):
            cleaned.append(cells)
            max_cols = max(max_cols, len(cells))
    if not cleaned or max_cols == 0:
        return ""
    for r in cleaned:
        r.extend([""] * (max_cols - len(r)))
    lines = [
        "| " + " | ".join(cleaned[0]) + " |",
        "| " + " | ".join(["---"] * max_cols) + " |",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in cleaned[1:])
    return "\n".join(lines)


def _extract_csv_enhanced(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(MAX_FILE_SIZE)
    reader = csv.reader(io.StringIO(sample))
    rows = []
    for row in reader:
        if any(cell.strip() for cell in row):
            rows.append(row)
            if len(rows) >= 2000:
                break
    return _format_table_as_markdown(rows)


def _extract_pdf_enhanced(filepath: str) -> str:
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                table_mds, bboxes = [], []
                try:
                    for t in page.find_tables():
                        bboxes.append(t.bbox)
                        md = _format_table_as_markdown(t.extract() or [])
                        if md:
                            table_mds.append(md)
                except Exception:
                    pass
                if bboxes:
                    filtered = page.filter(lambda o: not any(
                        not (o.get("x1", 0) < b[0] or o.get("x0", 0) > b[2] or o.get("bottom", 0) < b[1] or o.get("top", 0) > b[3])
                        for b in bboxes
                    ))
                    text = filtered.extract_text(x_tolerance=2, y_tolerance=2) or ""
                else:
                    text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                parts = []
                if text.strip():
                    c = _RE_PDF_ADJACENT.sub(r"\1 \2", _RE_PDF_NEWLINES.sub(" ", text))
                    parts.append(c.strip())
                parts.extend(table_mds)
                if parts:
                    pages.append("\n\n".join(parts))
        return "\n\n".join(pages)
    except Exception:
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            return "\n\n".join(
                _RE_PDF_ADJACENT.sub(r"\1 \2", _RE_PDF_NEWLINES.sub(" ", (p.extract_text() or ""))).strip()
                for p in reader.pages if p.extract_text()
            )
        except Exception:
            return ""


def _extract_docx_enhanced(filepath: str) -> str:
    import docx
    doc = docx.Document(filepath)
    parts = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style else ""
        if not isinstance(style_name, str):
            style_name = ""
        parts.append(f"\n## {text}\n" if style_name.startswith("Heading") else text)
    for table in doc.tables:
        md = _format_table_as_markdown([[c.text for c in r.cells] for r in table.rows])
        if md:
            parts.append(md)
    return "\n\n".join(parts)


def _extract_xlsx_enhanced(filepath: str) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    try:
        sheets = []
        for sheet in wb.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                if any(v is not None and str(v).strip() for v in row):
                    rows.append([v if v is not None else "" for v in row])
                    if len(rows) >= 2000:
                        break
            md = _format_table_as_markdown(rows)
            if md:
                sheets.append(f"## Sheet: {sheet.title}\n\n{md}")
        return "\n\n".join(sheets)
    finally:
        wb.close()


def _extract_pptx_enhanced(filepath: str) -> str:
    from pptx import Presentation
    prs = Presentation(filepath)
    slides = []
    for i, slide in enumerate(prs.slides):
        s_parts = [f"## Slide {i+1}"]
        for shape in slide.shapes:
            text_frame = getattr(shape, "text_frame", None)
            table = getattr(shape, "table", None)
            if shape.has_text_frame and text_frame is not None:
                s_parts.extend(p.text.strip() for p in text_frame.paragraphs if p.text.strip())
            elif shape.has_table and table is not None:
                md = _format_table_as_markdown([[c.text for c in r.cells] for r in table.rows])
                if md:
                    s_parts.append(md)
        if len(s_parts) > 1:
            slides.append("\n".join(s_parts))
    return "\n\n".join(slides)


def _is_markdown_table(text: str) -> bool:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return len(lines) >= 2 and lines[0].startswith("|") and lines[0].endswith("|")


def semantic_chunk(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    document_title: str = "",
) -> list[str]:
    """
    Section-aware and sentence-window-preserving semantic chunker.
    Preserves Markdown table headers across splits and maintains sliding sentence overlap.
    """
    if not text or len(text.strip()) < 30:
        return []

    matches = list(_RE_MARKDOWN_HEADERS.finditer(text))
    sections: list[tuple[str, str]] = []
    if not matches:
        sections.append(("", text))
    else:
        if matches[0].start() > 0:
            sections.append(("", text[: matches[0].start()].strip()))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append((m.group(2).strip(), text[m.end() : end].strip()))

    all_chunks: list[str] = []
    for header, body in sections:
        if not body:
            continue
        bc = f"[{' > '.join(filter(None, [document_title, header]))}]\n" if (document_title or header) else ""
        avail = max(120, chunk_size - len(bc))
        current_sents: list[str] = []
        current_len = 0

        def _flush_sentences(keep_overlap: bool = True):
            nonlocal current_sents, current_len
            if not current_sents:
                return
            chunk_body = " ".join(current_sents).strip()
            if chunk_body:
                all_chunks.append((bc + chunk_body).strip())
            if keep_overlap and overlap > 0:
                overlap_sents: list[str] = []
                o_len = 0
                for s in reversed(current_sents):
                    if o_len + len(s) + 1 <= overlap or not overlap_sents:
                        overlap_sents.insert(0, s)
                        o_len += len(s) + 1
                    else:
                        break
                current_sents = overlap_sents
                current_len = o_len
            else:
                current_sents = []
                current_len = 0

        for raw_p in body.split("\n\n"):
            p = raw_p.strip()
            if not p:
                continue
            if _is_markdown_table(p):
                _flush_sentences(keep_overlap=False)
                lines = [ln.strip() for ln in p.splitlines() if ln.strip()]
                hdr = "\n".join(lines[:2]) + "\n"
                rows_acc: list[str] = []
                for row in lines[2:]:
                    if len(bc) + len(hdr) + len("\n".join(rows_acc + [row])) > chunk_size and rows_acc:
                        all_chunks.append((bc + hdr + "\n".join(rows_acc)).strip())
                        rows_acc = [row]
                    else:
                        rows_acc.append(row)
                if rows_acc:
                    all_chunks.append((bc + hdr + "\n".join(rows_acc)).strip())
                continue

            p_norm = _RE_CHUNK_WHITESPACE.sub(" ", p)
            sentences = [s.strip() for s in _RE_CHUNK_SENTENCES.split(p_norm) if s.strip()]
            for sent in sentences:
                if len(sent) > avail:
                    # Hard split ultra-long run-on lines with character overlap
                    _flush_sentences(keep_overlap=False)
                    step = max(60, avail - overlap)
                    for idx in range(0, len(sent), step):
                        sub = sent[idx : idx + avail].strip()
                        if len(sub) > 25:
                            all_chunks.append((bc + sub).strip())
                    continue

                if current_len + len(sent) + 1 <= avail:
                    current_sents.append(sent)
                    current_len += len(sent) + 1
                else:
                    _flush_sentences(keep_overlap=True)
                    current_sents.append(sent)
                    current_len += len(sent) + 1

        _flush_sentences(keep_overlap=False)

    return [c for c in all_chunks if len(c) > 25]


def index_document(filepath: str) -> bool:
    if not os.path.exists(filepath):
        return False
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return False
    try:
        if not (0 < os.path.getsize(filepath) <= MAX_FILE_SIZE):
            return False
    except OSError:
        return False

    text = extract_text(filepath)
    if not text or len(text.strip()) < 20:
        return False

    canonical_path = os.path.realpath(os.path.abspath(filepath))
    filename = os.path.basename(canonical_path)
    chunks = semantic_chunk(text, document_title=filename)
    if not chunks:
        return False

    try:
        col = _col(DOCUMENTS)
        if not col:
            return False

        try:
            old = col.get(where={"filepath": canonical_path}, include=[])
            if old and old.get("ids"):
                col.delete(ids=old["ids"])
        except Exception:
            pass

        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(canonical_path)).isoformat()
        ids = [hashlib.md5(f"{canonical_path}::{i}".encode()).hexdigest() for i in range(len(chunks))]
        metas = [{
            "filepath": canonical_path,
            "filepath_lower": canonical_path.lower(),
            "filename": filename,
            "filename_lower": filename.lower(),
            "chunk_index": i,
            "total_chunks": len(chunks),
            "file_type": ext.lstrip("."),
            "modified_time": mtime,
            "type": "document",
        } for i in range(len(chunks))]

        for i in range(0, len(ids), 100):
            b_docs = chunks[i : i + 100]
            b_embs = _embedding_fn([f"search_document: {d}" for d in b_docs]) if _embedding_fn else None
            kwargs: dict[str, Any] = {
                "ids": ids[i : i + 100],
                "documents": b_docs,
                "metadatas": metas[i : i + 100],
            }
            if b_embs:
                kwargs["embeddings"] = b_embs
            col.upsert(**kwargs)

        clear_search_cache()
        _schedule_bm25_rebuild()
        return True
    except Exception as e:
        logger.error("[RAG v2] Indexing error for '%s': %s", filepath, e)
        return False


def index_email(subject: str, sender: str, body_preview: str, date: str, is_read: bool = True, folder: str = "Inbox") -> None:
    doc_text = f"Subject: {subject}\nFrom: {sender}\nDate: {date}\n{body_preview}"
    doc_id = hashlib.md5(f"{subject}::{sender}::{date}".encode()).hexdigest()
    try:
        col = _col(EMAILS)
        if col:
            col.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{"subject": (subject or "")[:200], "sender": (sender or "")[:100], "date": date, "is_read": is_read, "folder": folder, "type": "email"}],
            )
    except Exception as e:
        logger.error("[RAG v2] Error indexing email: %s", e)


def index_calendar_event(subject: str, start: str, end: str, location: str = "", body: str = "") -> None:
    doc_text = f"Event: {subject}\nWhen: {start} to {end}" + (f"\nWhere: {location}" if location else "") + (f"\n{body[:300]}" if body else "")
    doc_id = hashlib.md5(f"{subject}::{start}".encode()).hexdigest()
    try:
        col = _col(CALENDAR)
        if col:
            col.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{"subject": (subject or "")[:200], "start": start, "end": end, "location": (location or "")[:200], "type": "calendar_event"}],
            )
    except Exception as e:
        logger.error("[RAG v2] Error indexing calendar event: %s", e)


# ═══════════════════════════════════════════════════════════════
#  9. Profile Prompts & Lifecycle Helpers (Non-Polluting)
# ═══════════════════════════════════════════════════════════════

def _clean_stt_entity(raw: str) -> str:
    # Stop at punctuation or clause boundaries so STT run-on sentences aren't captured as names/cities
    clause = re.split(r"[.!?,;:]|\b(?:and|but|because|so|right|now|today|please)\b", raw.strip(), maxsplit=1, flags=re.I)[0]
    tokens = [t for t in _RE_WORDS.findall(clause.strip()) if len(t) >= 2]
    return " ".join(tokens[:3]).title() if tokens else ""


def extract_user_profile_updates(user_query: str, remember: str = "") -> None:
    text = (user_query or "").strip()
    if not text:
        return

    profile = load_profile()
    identity = profile.setdefault("identity", {"name": "", "role": ""})
    preferences = profile.setdefault("preferences", {})
    updated = False

    if m := re.search(r"\b(?:my name is|call me)\s+([A-Za-z][A-Za-z\s]{1,30})", text, re.I):
        name = _clean_stt_entity(m.group(1))
        if name and 2 <= len(name) <= 25 and not any(ch.isdigit() for ch in name):
            identity["name"] = name
            add_user_fact(f"User's name is {name}", category="identity", importance=0.95)
            updated = True

    if m := re.search(r"\b(?:my (?:favorite|favourite) artist is|i love listening to)\s+([A-Za-z0-9][A-Za-z0-9\s]{1,35})", text, re.I):
        artist = _clean_stt_entity(m.group(1))
        if artist:
            favs = preferences.setdefault("favorite_artists", [])
            if artist not in favs:
                favs.append(artist)
                preferences["favorite_artists"] = favs[-10:]
                add_user_fact(f"Favorite artist: {artist}", category="preference", importance=0.75)
                updated = True

    if m := re.search(r"\b(?:i live in|my city is)\s+([A-Za-z][A-Za-z\s]{1,30})", text, re.I):
        city = _clean_stt_entity(m.group(1))
        if city:
            preferences["favorite_city"] = city
            add_user_fact(f"User lives in {city}", category="preference", importance=0.8)
            updated = True

    if remember and len(remember.strip()) > 5:
        add_user_fact(remember.strip(), category="custom", importance=0.85)
        updated = True

    if updated:
        save_profile(profile)


def get_user_profile_prompt(query: str = "", semantic_search: bool = True) -> str:
    """
    Build user profile prompt without forcing the LLM to hallucinate connections
    to unrelated personal facts.
    """
    profile = load_profile()
    parts: list[str] = []
    if name := profile.get("identity", {}).get("name"):
        parts.append(f"User's name: {name} (address them naturally only when appropriate).")
    if city := profile.get("preferences", {}).get("favorite_city"):
        parts.append(f"User's location: {city}.")

    if semantic_search and query and not _is_conversational_filler(query):
        try:
            matched = search(query, target_collections=[USER_FACTS], top_k=3)
            for mf in matched:
                doc = (mf.get("text") or "").strip()
                # Strict grounding check so random facts don't pollute general queries
                if doc and _is_chunk_grounded(mf, floor=0.58):
                    parts.append(f"Relevant user memory: {doc}")
        except Exception:
            pass

    if not parts:
        return ""

    return (
        "<user_profile_context>\n"
        "Background info about the user (use ONLY if directly relevant to their question; otherwise ignore):\n"
        + "\n".join(parts)
        + "\n</user_profile_context>"
    )


def get_active_context_prompt() -> str:
    state = get_active_state(clean_expired=True)
    parts: list[str] = []
    if last_link := state.get("last_link"):
        if u := last_link.get("url"):
            t = last_link.get("title") or "Website Link"
            parts.append(f"Active Link: {u} ({t})")
    if media := state.get("current_media"):
        if title := media.get("title") or media.get("query"):
            parts.append(f"Playing: '{title}' on {media.get('platform', 'YouTube')}")
    if app := state.get("active_app", {}).get("name"):
        parts.append(f"Active App: {app}")
    if f := state.get("active_file", {}).get("name"):
        parts.append(f"Active File: {f}")
    return f"[PC Environment State: {' | '.join(parts)}]" if parts else ""


def get_index_stats() -> dict:
    stats = {}
    for name in ALL_COLLECTIONS:
        collection = _col(name)
        stats[name] = collection.count() if collection is not None else 0
    stats["total"] = sum(stats.values())
    return stats


def load_memory() -> dict:
    profile = load_profile()
    return {
        "active_state": get_active_state(clean_expired=True),
        "ui_settings": profile.get("ui_settings", {}),
        "conversations": get_all_conversations(limit=200),
        "user_profile": {
            "identity": profile.get("identity", {}),
            "preferences": profile.get("preferences", {}),
            "custom_facts": get_all_user_facts(),
        },
        "interaction_style": profile.get("stats", {}),
    }


def save_memory(memory: dict) -> None:
    profile = load_profile()
    if isinstance(memory.get("ui_settings"), dict):
        profile["ui_settings"] = memory["ui_settings"]
    if isinstance(up := memory.get("user_profile"), dict):
        if "identity" in up:
            profile["identity"] = up["identity"]
        if "preferences" in up:
            profile["preferences"] = up["preferences"]
        for fact in up.get("custom_facts", []):
            if isinstance(fact, dict):
                add_user_fact(fact.get("text", ""), category=fact.get("category", "custom"), importance=fact.get("importance", 1.0))
            elif isinstance(fact, str):
                add_user_fact(fact, category="custom")
    save_profile(profile)


def init_rag(background: bool = True) -> None:
    global _initialized, _init_failed, _init_error
    if _initialized or _init_failed:
        return

    def _worker():
        global _initialized, _init_failed, _init_error
        try:
            _init_chroma()
        except Exception as e:
            _init_failed, _initialized, _init_error = True, False, e

    if background:
        _executor.submit(_worker)
    else:
        _worker()
        if _init_error:
            raise RAGEngineError("RAG engine initialization failed", _init_error) from _init_error


def get_init_error() -> Exception | None:
    return _init_error


def search_async(query: str, target_collections: list[str] | None = None, top_k: int = 5, callback=None):
    def _task():
        try:
            res = search(query, target_collections, top_k)
            if callback:
                callback(res)
        except Exception as e:
            if callback:
                callback(RAGEngineError("Search failed", e))
    _executor.submit(_task)


def flush_write_queues() -> None:
    if _profile_cache is not None:
        _persist_profile_sync(_profile_cache)


atexit.register(flush_write_queues)
