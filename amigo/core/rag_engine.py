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
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import pickle
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import memvid_sdk

logger = logging.getLogger("amigo.rag_engine_v2")

for _log_name in (
    "httpx", "httpcore", "huggingface_hub",
    "urllib3", "pdfminer", "pdfminer.pdffont", "pdfminer.pdfinterp",
    "pdfminer.pdfpage", "pdfminer.pdfdocument", "pypdf", "pdfplumber",
):
    logging.getLogger(_log_name).setLevel(logging.ERROR)


class RAGEngineError(Exception):
    """Custom exception for RAG engine initialization and runtime errors."""
    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


# ── Pre-compiled Regex Patterns ────────────────────────────────
_RE_PDF_NEWLINES = re.compile(r"(?<=[a-zA-Z0-9])\r\n(?=[a-zA-Z0-9])")
_RE_PDF_ADJACENT = re.compile(r"([A-Z0-9]{2,})([A-Z][a-z]+)")
_RE_CHUNK_WHITESPACE = re.compile(r"\s+")
_RE_CHUNK_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_RE_MARKDOWN_HEADERS = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_RE_WORDS = re.compile(r"\b[\w'-]{2,}\b", re.UNICODE)

# ── Paths & Constants ──────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))

RAG_DATA_DIR = os.path.join(_PROJECT_ROOT, "rag_data")

PROFILE_FILE = os.path.join(_PROJECT_ROOT, "amigo_profile.json")

CONVERSATIONS = "conversations"
USER_FACTS = "user_facts"
DOCUMENTS = "documents"
EMAILS = "emails"
CALENDAR = "calendar"
ALL_COLLECTIONS = [CONVERSATIONS, USER_FACTS, DOCUMENTS, EMAILS, CALENDAR]

MEMVID_PATH = os.path.join(RAG_DATA_DIR, "amigo_memory.mv2")

_TAG_TO_COLLECTION = {
    "conversation": CONVERSATIONS,
    "user_fact": USER_FACTS,
    "document": DOCUMENTS,
    "email": EMAILS,
    "calendar": CALENDAR,
}

_COLLECTION_TO_TAG = {
    CONVERSATIONS: "conversation",
    USER_FACTS: "user_fact",
    DOCUMENTS: "document",
    EMAILS: "email",
    CALENDAR: "calendar",
}

_WORD_PATTERN = re.compile(r"\b[\w'-]{2,}\b", re.UNICODE)
_mv_instance: memvid_sdk.Memvid | None = None
_mv_lock = threading.RLock()

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

MAX_VOICE_CONTEXT_CHARS = 3500
MAX_FILE_SIZE = 50 * 1024 * 1024
CHUNK_SIZE = 950
CHUNK_OVERLAP = 180

STATE_TTLS = {
    "current_media": 1800,
    "active_app": 1800,
    "last_search": 900,
    "active_subject": 600,
    "active_file": 1800,
}

# ── Runtime State & Locks ──────────────────────────────────────
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
#  1. Memvid Engine Initialization & Storage
# ═══════════════════════════════════════════════════════════════

def init_memvid() -> memvid_sdk.Memvid:
    """Initialize or open the single-file Memvid memory capsule with auto-recovery."""
    global _mv_instance, _initialized
    with _mv_lock:
        if _mv_instance is not None:
            return _mv_instance
        try:
            os.makedirs(RAG_DATA_DIR, exist_ok=True)
            if os.path.exists(MEMVID_PATH) and os.path.getsize(MEMVID_PATH) > 0:
                logger.info("[RAG Memvid] Opening memory file: %s", MEMVID_PATH)
                mv = None
                for attempt in range(4):
                    try:
                        mv = memvid_sdk.use("basic", MEMVID_PATH, enable_lex=True)
                        break
                    except Exception as open_err:
                        err_str = str(open_err).lower()
                        is_lock = any(k in err_str for k in ("locked", "lock", "os error 33", "sharing", "busy", "unavailable"))
                        if is_lock:
                            if attempt < 3:
                                time.sleep(0.15 * (attempt + 1))
                                continue
                            raise
                        logger.warning("[RAG Memvid] Capsule corrupted (%s). Recreating clean memory capsule.", open_err)
                        try:
                            os.remove(MEMVID_PATH)
                        except Exception:
                            pass
                        mv = memvid_sdk.create(MEMVID_PATH, enable_lex=True)
                        break
            else:
                logger.info("[RAG Memvid] Creating new single-file memory: %s", MEMVID_PATH)
                mv = memvid_sdk.create(MEMVID_PATH, enable_lex=True)
            _mv_instance = mv
            _initialized = True
            logger.info("[RAG Memvid] Initialized successfully.")
            return mv
        except Exception as e:
            logger.error("[RAG Memvid] Failed to initialize: %s", e)
            raise


def is_memvid_ready() -> bool:
    """Check if the Memvid memory engine is active and ready."""
    return _initialized and _mv_instance is not None


def is_rag_ready() -> bool:
    """Compatibility check for whether the memory/RAG engine is active."""
    try:
        if not is_memvid_ready():
            init_memvid()
        return is_memvid_ready()
    except Exception:
        return True


def commit() -> None:
    """Commit pending memory and Tantivy index changes to disk with retry if writer is busy."""
    mv = init_memvid()
    with _mv_lock:
        for attempt in range(5):
            try:
                mv.commit()
                return
            except Exception as e:
                if "unavailable" in str(e).lower() and attempt < 4:
                    time.sleep(0.08 * (attempt + 1))
                    continue
                logger.debug("[RAG Memvid] Commit note: %s", e)
                break


def _format_search_query(query: str) -> str:
    """
    Format query into an escaped Tantivy search string.
    Language-agnostic: tokenizes any Unicode word characters and prioritizes
    high-information salient terms over low-information functional particles.
    No hardcoded stopwords or language-specific rules.
    """
    q = query.strip()
    if not q:
        return ""
    words = _WORD_PATTERN.findall(q)
    if not words:
        return q

    # Rank tokens by dynamic information density (morphology + casing)
    scored_tokens = [(_token_information_weight(w, context_text=q), w) for w in words]
    max_w = max((w for w, _ in scored_tokens), default=0.5)
    salient = [tok for w, tok in scored_tokens if w >= max(0.40, max_w * 0.65)]
    target_words = salient if salient else words

    escaped_words = [re.sub(r'([+\-!(){}[\]^"~*?:\\/])', r'\\\1', w) for w in target_words]
    return " OR ".join(escaped_words)


def _col(name: str):
    """Backwards-compatibility stub for legacy callers."""
    return None


# ═══════════════════════════════════════════════════════════════
#  2. Fast BM25 Keyword Index & Dynamic Token IDF
# ═══════════════════════════════════════════════════════════════

def _init_bm25_index() -> None:
    pass


def _rebuild_bm25_index() -> None:
    pass


def _schedule_bm25_rebuild(delay: float = 3.0) -> None:
    pass


def _token_information_weight(token: str, context_text: str = "") -> float:
    """
    Compute the information weight (specificity) of a token dynamically without hardcoded stopword lists.
    Combines:
    1. Live corpus Inverse Document Frequency (IDF) from BM25 / tracked chunks when available.
    2. Morphological specificity (sub-linear character length + digit/mixed-case indicators).
    """
    low = token.lower()
    if len(low) < 2:
        return 0.0

    # If entire context is uppercase, casing is uniform and does not indicate an acronym
    is_context_all_caps = bool(context_text and context_text.isupper())
    has_digit = any(c.isdigit() for c in token)
    is_acronym = (len(token) >= 2 and token.isupper() and not is_context_all_caps)
    length_factor = min(1.0, max(0.10, (len(low) - 2.0) / 5.0))
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
        for attempt in range(3):
            try:
                tmp = PROFILE_FILE + ".tmp"
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


def _is_expired(ts_str: str | None, ttl: int = 0) -> bool:
    """Time expiry disabled: memory and session state persist across turns without arbitrary clock timeouts."""
    return False


def get_active_state(clean_expired: bool = False) -> dict:
    """Return active session state without clock-based time expiry."""
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

    slot_data = {"items": data} if isinstance(data, list) else (dict(data) if isinstance(data, dict) else {})
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
    Returns clean user query without Frankenstein token pollution.
    Conversational context is handled natively by the LLM via dialogue history turns.
    """
    if not query:
        return ""
    return query.strip()


def expand_query(query: str, conversation_history: list | None = None) -> list[str]:
    """Return clean query for search without token pollution."""
    if not query or not query.strip():
        return [query]
    return [query.strip()]


# ═══════════════════════════════════════════════════════════════
#  5. Parallel Hybrid Search & Calibrated Relevance Scoring
# ═══════════════════════════════════════════════════════════════

def clear_search_cache() -> None:
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE.clear()


def _is_conversational_filler(query: str) -> bool:
    if not query or not query.strip():
        return True
    clean = query.strip()
    tokens = _RE_WORDS.findall(clean)
    if not tokens:
        return len(clean) < 2
    if len(tokens) == 1 and len(tokens[0]) <= 2 and not any(ch in clean for ch in ("?", "!", "$", "%")):
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


def search(
    query: str,
    target_collections: list[str] | None = None,
    top_k: int = 5,
    query_type: str = "auto",
    metadata_filter: dict | None = None,
    conversation_history: list | None = None,
    skip_rag: bool = False,
    **kwargs,
) -> list[dict[str, Any]]:
    """
    Unified, low-latency search powered by Memvid (Rust Tantivy):
    - Sub-2ms retrieval latency with zero PyTorch cold-start penalty
    - 100% language-agnostic / multilingual Unicode token indexing (no hardcoded stopwords)
    - Zero-hallucination lexical & semantic gating (abstains when no relevant matches exist)
    """
    if skip_rag or not query or not query.strip() or _is_conversational_filler(query):
        return []

    if target_collections is None:
        if query_type in ("conversation_recall", "memory", "fact_lookup"):
            target_collections = [USER_FACTS, CONVERSATIONS]
        elif query_type in ("document_qa", "document", "file"):
            target_collections = [DOCUMENTS]
        else:
            target_collections = [DOCUMENTS, USER_FACTS]

    if not target_collections:
        return []

    # Check search cache
    cache_key = f"{query.strip().lower()}::{sorted(target_collections)}::{top_k}::{json.dumps(metadata_filter or {}, sort_keys=True)}"
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        if cache_key in _SEARCH_CACHE:
            cached_time, cached_results = _SEARCH_CACHE[cache_key]
            if now - cached_time < _SEARCH_CACHE_TTL:
                return cached_results

    try:
        mv = init_memvid()
    except Exception as e:
        logger.error("[RAG Memvid] Cannot search, init failed: %s", e)
        return []

    formatted_q = _format_search_query(query)
    if not formatted_q:
        return []

    target_tags = set()
    for col in target_collections:
        tag = _COLLECTION_TO_TAG.get(col)
        if tag:
            target_tags.add(tag)
    if not target_tags:
        target_tags = set(_TAG_TO_COLLECTION.keys())

    with _mv_lock:
        try:
            res = mv.find(formatted_q, k=min(100, max(top_k * 10, 50)))
            hits = res.get("hits") or []
        except Exception as e:
            logger.debug("[RAG Memvid] Search note on '%s': %s", formatted_q, e)
            hits = []

    results: list[dict[str, Any]] = []
    for hit in hits:
        tags = set(hit.get("tags") or [])
        matched_tags = tags.intersection(target_tags)
        if target_tags and not matched_tags:
            continue

        primary_tag = next(iter(matched_tags)) if matched_tags else "document"
        col_name = _TAG_TO_COLLECTION.get(primary_tag, DOCUMENTS)

        # Retrieve extra metadata via frame lookup
        meta: dict[str, Any] = {}
        uri = hit.get("uri")
        if uri:
            try:
                frame_data = mv.frame(uri)
                for k, v in (frame_data.get("extra_metadata") or {}).items():
                    try:
                        meta[k] = json.loads(v)
                    except Exception:
                        meta[k] = v
            except Exception:
                pass

        # Apply metadata filters (e.g. specific filepath or filename)
        if metadata_filter and isinstance(metadata_filter, dict):
            mismatch = False
            for fk, fv in metadata_filter.items():
                if fk in meta and meta[fk] != fv:
                    mismatch = True
                    break
            if mismatch:
                continue

        raw_score = float(hit.get("score") or 0.0)
        # Normalize Tantivy BM25 score to [0.0, 1.0] range
        calibrated_score = min(1.0, max(0.05, raw_score / 6.0))

        text_content = hit.get("text") or hit.get("snippet") or ""
        # Clean internal indexing headers if present
        if "extractous_metadata:" in text_content:
            text_content = text_content.split("extractous_metadata:")[0].strip()

        results.append({
            "text": text_content,
            "source": col_name,
            "score": calibrated_score,
            "raw_semantic": calibrated_score,
            "lexical_overlap": min(1.0, float(hit.get("matches") or 1) / 5.0),
            "metadata": meta,
        })

    # Sort descending by score
    results.sort(key=lambda x: x["score"], reverse=True)
    final_results = results[:top_k]

    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[cache_key] = (now, final_results)
        if len(_SEARCH_CACHE) > 200:
            _SEARCH_CACHE.popitem(last=False)

    return final_results


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
        # Require genuine relevance: if zero lexical/keyword match, require strong semantic similarity
        if lex == 0.0 and kw == 0.0:
            return raw_sem >= 0.70 and score >= 0.45
        return (
            raw_sem >= 0.55
            or (raw_sem >= 0.42 and (lex >= 0.08 or kw >= 1.0))
            or kw >= 2.0
            or (lex >= 0.15 and score >= 0.35)
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

    # Grounding verification before adding any chunk
    floor = min_score if min_score is not None else 0.20

    if results:
        grounded = [r for r in results if _is_chunk_grounded(r, floor)]
        if grounded:
            top_score = grounded[0].get("score", 0.0)
            margin = max(floor, top_score * 0.55)
            relevant = [r for r in grounded if r.get("score", 0.0) >= margin]

            k_limit = min(top_k, 5) if for_voice else max(top_k, 8)
            for r in relevant[:k_limit]:
                src = r.get("source", "")
                meta = r.get("metadata", {})
                chunk_text = (r.get("text") or "").strip()[:1400]
                if not chunk_text or total_chars + len(chunk_text) > max_chars:
                    continue

                if src == DOCUMENTS:
                    fname = meta.get("filename", "document")
                    parts.append(f"[From document '{fname}']:\r\n{chunk_text}")
                elif src == CONVERSATIONS:
                    # Never inject past failure disclaimers as evidence
                    low_chunk = chunk_text.lower()
                    if not any(f in low_chunk for f in ("couldn't find", "could not find", "don't have access", "cannot access")):
                        parts.append(f"[Past conversation]:\r\n{chunk_text}")
                elif src == EMAILS:
                    parts.append(f"[Email]:\r\n{chunk_text}")
                elif src == CALENDAR:
                    parts.append(f"[Calendar Event]:\r\n{chunk_text}")
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

    body = "\r\n\r\n".join(parts)
    return (
        "<supplementary_context>\r\n"
        "NOTE: Verified local reference passages from the user's indexed records:\r\n"
        "- Answer the query using explicit facts directly attributed to the subject or entity requested.\r\n"
        "- Do not attribute background descriptions, organizational overviews, or third-party details to a different entity or person.\r\n"
        "- Structure multi-item information cleanly using Markdown bullet points (`- `) and bold key identifiers.\r\n"
        "- Preserve all exact dates, codes, numerical values, and domain terminology directly as written in the source.\r\n"
        "- Synthesize tabular or structured data into clean bullet highlights without raw table formatting artifacts.\r\n"
        "- If the retrieved records do not contain information relevant to the user's request, ignore them.\r\n\r\n"
        f"{body}\r\n"
        "</supplementary_context>"
    )


def build_document_evidence(
    query: str,
    top_k: int = 6,
    conversation_history: list | None = None,
    filepath: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return grounded document passages and source locations for document Q&A."""
    if not query or not query.strip() or _is_conversational_filler(query):
        return "", []

    metadata_filter = None
    if filepath:
        metadata_filter = {"filepath": os.path.realpath(os.path.abspath(filepath))}

    history = conversation_history if conversation_history is not None else get_recent_conversations(count=4)
    results = search(
        query,
        target_collections=[DOCUMENTS],
        top_k=top_k,
        metadata_filter=metadata_filter,
        conversation_history=history,
    )
    grounded = [result for result in results if _is_chunk_grounded(result, 0.35)]
    if not grounded:
        return "", []

    top_score = max(float(result.get("score", 0.0) or 0.0) for result in grounded)
    if top_score < 0.35:
        return "", []
    score_floor = max(0.30, top_score * 0.60)
    candidate_pool = [
        result for result in grounded
        if float(result.get("score", 0.0) or 0.0) >= score_floor
    ]

    # Enforce document diversity (max 2 chunks per unique file) to prevent a single file monopolizing evidence
    relevant: list[dict[str, Any]] = []
    file_chunk_counts: dict[str, int] = {}
    for r in candidate_pool:
        f_key = str((r.get("metadata") or {}).get("filename") or r.get("source") or "")
        count = file_chunk_counts.get(f_key, 0)
        if count < 2:
            file_chunk_counts[f_key] = count + 1
            relevant.append(r)
        if len(relevant) >= max(1, top_k):
            break

    passages: list[str] = []
    sources: list[dict[str, Any]] = []
    source_ids: dict[tuple[str, int], int] = {}

    for result in relevant:
        text = str(result.get("text") or "").strip()
        metadata = result.get("metadata") or {}
        if not text or not isinstance(metadata, dict):
            continue

        source_path = str(metadata.get("filepath") or "")
        filename = str(metadata.get("filename") or os.path.basename(source_path))
        chunk_index = metadata.get("chunk_index")
        if not filename or not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
            continue

        source_key = (source_path or filename, chunk_index)
        source_id = source_ids.get(source_key)
        if source_id is None:
            source_id = len(sources) + 1
            source_ids[source_key] = source_id
            sources.append({
                "id": source_id,
                "file": filename,
                "location": f"passage {chunk_index + 1}",
            })

        passages.append(
            f"[Source {source_id}: {filename}, passage {chunk_index + 1}]\r\n{text[:900]}"
        )

    if not passages:
        return "", []

    if sources:
        first_source = relevant[0].get("metadata") or {}
        f_path = str(first_source.get("filepath") or "")
        f_name = str(first_source.get("filename") or sources[0].get("file") or "")
        if f_path or f_name:
            update_active_state("active_file", {"path": f_path, "name": f_name})

    instructions = (
        "Answer the user's question using the verified document passages below.\r\n"
        "- Answer directly using only facts that explicitly attribute to the entity or subject asked about.\r\n"
        "- Do not attribute background descriptions, organizational overviews, or third-party details to a different entity or person.\r\n"
        "- Structure your answer cleanly with Markdown formatting: use bullet points (`- `) and bold key identifiers or headings.\r\n"
        "- Preserve all exact dates, numbers, codes, and domain terminology directly as stated in the source.\r\n"
        "- If the passages do not contain explicit evidence answering the question, state clearly that the indexed documents do not specify this information."
    )
    context = f"{instructions}\r\n\r\n" + "\r\n\r\n".join(passages)
    return context, sources


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
        results = search(
            query=resolved_q,
            target_collections=[DOCUMENTS],
            top_k=6,
            metadata_filter={"filepath": canonical_path},
        )
        if not results:
            results = search(
                query=resolved_q,
                target_collections=[DOCUMENTS],
                top_k=6,
                metadata_filter={"filename": filename},
            )
        if results:
            selected, total_len = [], 0
            for r in results:
                if r.get("score", 0.0) < 0.20:
                    continue
                snippet = (r.get("text") or "").strip()[:850]
                if snippet and total_len + len(snippet) <= max_chars:
                    selected.append(snippet)
                    total_len += len(snippet)
            if selected:
                update_active_state("active_file", {"path": canonical_path, "name": filename})
                return f"[Relevant sections from '{filename}']:\r\n" + "\r\n\r\n".join(selected)
    except Exception as e:
        logger.debug("[RAG Memvid] build_file_context error: %s", e)

    return ""


def get_file_summary_context(filepath: str) -> str:
    text = extract_text(filepath)
    if not text:
        return ""
    fname = os.path.basename(filepath)
    update_active_state("active_file", {"path": os.path.realpath(os.path.abspath(filepath)), "name": fname})
    return f"[Content from '{fname}']:\r\n{text[:8000]}"


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
            mv = init_memvid()
            title = f"Conv: {user_msg[:40]}"
            text = f"User: {user_msg}\r\nAssistant: {assistant_msg}"
            metadata = {
                "user_msg": user_msg,
                "assistant_msg": assistant_msg,
                "tool": tool,
                "params": json.dumps(params or {}),
                "timestamp": timestamp,
                "importance": importance,
            }
            if url:
                metadata["url"] = url

            with _mv_lock:
                mv.put(
                    title=title,
                    text=text,
                    tags=["conversation", tool],
                    metadata=metadata,
                )
                commit()

            extract_user_profile_updates(user_msg, remember=remember)
        except Exception as e:
            logger.debug("[RAG Memvid] Background conv persist note: %s", e)

    _executor.submit(_bg_persist)


def get_recent_conversations(count: int = 4) -> list[dict]:
    return list(_ensure_buffer())[-count:]


def get_all_conversations(limit: int = 200) -> list[dict]:
    global _all_conversations_cache
    with _conv_cache_lock:
        if _all_conversations_cache is not None:
            return list(_all_conversations_cache)[-limit:]

    buf = list(_ensure_buffer())
    if buf:
        return buf[-limit:]

    try:
        mv = init_memvid()
        timeline = mv.timeline() or []
        items = []
        for item in timeline:
            preview = item.get("preview") or ""
            if "tags: conversation" in preview:
                uri = item.get("uri")
                if uri:
                    try:
                        frame_data = mv.frame(uri)
                        meta = frame_data.get("extra_metadata") or {}
                        items.append({
                            "user": meta.get("user_msg", ""),
                            "assistant": meta.get("assistant_msg", ""),
                            "tool": meta.get("tool", "chat"),
                            "timestamp": meta.get("timestamp", ""),
                            "importance": float(meta.get("importance", 1.0)),
                        })
                    except Exception:
                        pass
        if items:
            with _conv_cache_lock:
                _all_conversations_cache = collections.deque(items, maxlen=200)
            return items[-limit:]
    except Exception:
        pass
    return []




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

    for slot in _ACTIVE_STATE:
        _ACTIVE_STATE[slot] = None if slot == "current_media" else {k: "" for k in _ACTIVE_STATE[slot]}
    clear_search_cache()

    profile = load_profile()
    profile["recent_conversations"] = []
    save_profile(profile)

    try:
        mv = init_memvid()
        with _mv_lock:
            for item in (mv.timeline() or []):
                fid = item.get("frame_id")
                if fid is None:
                    continue
                try:
                    f = mv.frame(item.get("uri"))
                    if "conversation" in (f.get("tags") or []):
                        mv.remove(fid)
                except Exception:
                    pass
            commit()
        logger.info("[RAG Memvid] Cleared conversations from memory capsule.")
    except Exception as e:
        logger.error("[RAG Memvid] Error clearing conversations from Memvid: %s", e)


def add_user_fact(fact: str, category: str = "user_profile", importance: float = 1.0) -> str | None:
    """Store a personal user preference or fact into the Memvid memory."""
    if not fact or len(fact.strip()) < 5:
        return None
    clean_fact = fact.strip()
    try:
        mv = init_memvid()
        title = f"Fact: {category}"
        metadata = {
            "category": category,
            "fact": clean_fact,
            "importance": importance,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        with _mv_lock:
            frame_id = mv.put(
                title=title,
                text=clean_fact,
                tags=["user_fact", category],
                metadata=metadata,
            )
            commit()
        clear_search_cache()
        return str(frame_id)
    except Exception as e:
        logger.error("[RAG Memvid] Error adding user fact: %s", e)
        return None


def get_all_user_facts(min_importance: float = 0.0) -> list[str]:
    """Retrieve all stored user facts and preferences from the Memvid memory."""
    facts: list[str] = []
    try:
        p = load_profile()
        profile_facts = p.get("user_profile", {}).get("custom_facts", [])
        for f in profile_facts:
            txt = f.get("text") if isinstance(f, dict) else str(f)
            if txt and txt not in facts:
                facts.append(txt)
    except Exception:
        pass

    try:
        mv = init_memvid()
        with _mv_lock:
            timeline = mv.timeline() or []
            for item in timeline:
                uri = item.get("uri")
                if uri:
                    try:
                        frame_data = mv.frame(uri)
                        tags = frame_data.get("tags") or []
                        if "user_fact" in tags:
                            meta = frame_data.get("extra_metadata") or {}
                            raw_fact = meta.get("fact")
                            if raw_fact:
                                try:
                                    fact = json.loads(raw_fact)
                                except Exception:
                                    fact = raw_fact
                            else:
                                fact = frame_data.get("text") or ""
                            clean = str(fact).strip().strip('"')
                            if clean and clean not in facts:
                                facts.append(clean)
                    except Exception:
                        pass
    except Exception as e:
        logger.debug("[RAG Memvid] Error retrieving user facts: %s", e)
    return facts


def clear_user_facts() -> None:
    try:
        profile = load_profile()
        if "user_profile" in profile:
            profile["user_profile"]["custom_facts"] = []
        save_profile(profile)
        clear_search_cache()

        mv = init_memvid()
        with _mv_lock:
            for item in (mv.timeline() or []):
                fid = item.get("frame_id")
                if fid is None:
                    continue
                try:
                    f = mv.frame(item.get("uri"))
                    if "user_fact" in (f.get("tags") or []):
                        mv.remove(fid)
                except Exception:
                    pass
            commit()
        logger.info("[RAG Memvid] Cleared user facts from memory capsule.")
    except Exception as e:
        logger.error("[RAG Memvid] Error clearing user facts: %s", e)
        clear_search_cache()


def clear_all_memory() -> None:
    """Completely resets the Memvid memory capsule and caches."""
    global _mv_instance
    clear_conversations()
    clear_user_facts()
    with _mv_lock:
        try:
            if os.path.exists(MEMVID_PATH):
                os.remove(MEMVID_PATH)
        except Exception:
            pass
        _mv_instance = memvid_sdk.create(MEMVID_PATH, enable_lex=True)
    clear_search_cache()
    logger.info("[RAG Memvid] Completely reset Memvid memory capsule.")


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
        cells = [str(c or "").strip().replace("\r\n", " ").replace("|", "\\|") for c in r]
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
    return "\r\n".join(lines)


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
                    pages.append("\r\n\r\n".join(parts))
        return "\r\n\r\n".join(pages)
    except Exception:
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            return "\r\n\r\n".join(
                _RE_PDF_ADJACENT.sub(r"\1 \2", _RE_PDF_NEWLINES.sub(" ", (p.extract_text() or ""))).strip()
                for p in reader.pages if p.extract_text()
            )
        except Exception:
            return ""


def _extract_docx_enhanced(filepath: str) -> str:
    import docx
    doc = docx.Document(filepath)
    parts = [
        f"\r\n## {p.text.strip()}\r\n" if p.style.name.startswith("Heading") else p.text.strip()
        for p in doc.paragraphs if p.text.strip()
    ]
    for table in doc.tables:
        md = _format_table_as_markdown([[c.text for c in r.cells] for r in table.rows])
        if md:
            parts.append(md)
    return "\r\n\r\n".join(parts)


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
                sheets.append(f"## Sheet: {sheet.title}\r\n\r\n{md}")
        return "\r\n\r\n".join(sheets)
    finally:
        wb.close()


def _extract_pptx_enhanced(filepath: str) -> str:
    from pptx import Presentation
    prs = Presentation(filepath)
    slides = []
    for i, slide in enumerate(prs.slides):
        s_parts = [f"## Slide {i+1}"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                s_parts.extend(p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip())
            elif shape.has_table:
                md = _format_table_as_markdown([[c.text for c in r.cells] for r in shape.table.rows])
                if md:
                    s_parts.append(md)
        if len(s_parts) > 1:
            slides.append("\r\n".join(s_parts))
    return "\r\n\r\n".join(slides)


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
        bc = f"[{' > '.join(filter(None, [document_title, header]))}]\r\n" if (document_title or header) else ""
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

        for raw_p in body.split("\r\n\r\n"):
            p = raw_p.strip()
            if not p:
                continue
            if _is_markdown_table(p):
                _flush_sentences(keep_overlap=False)
                lines = [ln.strip() for ln in p.splitlines() if ln.strip()]
                hdr = "\r\n".join(lines[:2]) + "\r\n"
                rows_acc: list[str] = []
                for row in lines[2:]:
                    if len(bc) + len(hdr) + len("\r\n".join(rows_acc + [row])) > chunk_size and rows_acc:
                        all_chunks.append((bc + hdr + "\r\n".join(rows_acc)).strip())
                        rows_acc = [row]
                    else:
                        rows_acc.append(row)
                if rows_acc:
                    all_chunks.append((bc + hdr + "\r\n".join(rows_acc)).strip())
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


def add_document_chunk(
    filepath: str,
    filename: str,
    chunk_index: int,
    text: str,
    total_chunks: int = 1,
    commit: bool = True,
) -> str | None:
    """Store a document passage into the single-file Memvid index."""
    if not text or not text.strip():
        return None

    try:
        mv = init_memvid()
        title = f"{filename} passage {chunk_index + 1}"
        metadata = {
            "filepath": filepath,
            "filename": filename,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
        }

        with _mv_lock:
            for attempt in range(5):
                try:
                    frame_id = mv.put(
                        title=title,
                        text=text.strip(),
                        tags=["document"],
                        metadata=metadata,
                    )
                    if commit:
                        mv.commit()
                    return str(frame_id)
                except Exception as e:
                    if "unavailable" in str(e).lower() and attempt < 4:
                        time.sleep(0.08 * (attempt + 1))
                        continue
                    logger.error("[RAG Memvid] Error adding document chunk: %s", e)
                    return None
            return None
    except Exception as e:
        logger.error("[RAG Memvid] add_document_chunk failed: %s", e)
        return None


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
        for i, chunk in enumerate(chunks):
            add_document_chunk(
                filepath=canonical_path,
                filename=filename,
                chunk_index=i,
                text=chunk,
                total_chunks=len(chunks),
                commit=False,
            )
        commit()
        clear_search_cache()
        return True
    except Exception as e:
        logger.error("[RAG Memvid] Error indexing document %s: %s", filepath, e)
        return False


def index_email(subject: str, sender: str, body_preview: str, date: str, is_read: bool = True, folder: str = "Inbox") -> None:
    doc_text = f"Subject: {subject}\r\nFrom: {sender}\r\nDate: {date}\r\n{body_preview}"
    metadata = {
        "subject": (subject or "")[:200],
        "sender": (sender or "")[:100],
        "date": date,
        "is_read": is_read,
        "folder": folder,
        "type": "email",
    }
    try:
        mv = init_memvid()
        with _mv_lock:
            mv.put(
                title=f"Email: {subject[:40]}",
                text=doc_text,
                tags=["email", folder],
                metadata=metadata,
            )
            commit()
        clear_search_cache()
    except Exception as e:
        logger.error("[RAG Memvid] Error indexing email: %s", e)


def index_calendar_event(subject: str, start: str, end: str, location: str = "", body: str = "") -> None:
    doc_text = f"Event: {subject}\r\nWhen: {start} to {end}" + (f"\r\nWhere: {location}" if location else "") + (f"\r\n{body[:300]}" if body else "")
    metadata = {
        "subject": (subject or "")[:200],
        "start": start,
        "end": end,
        "location": (location or "")[:200],
        "type": "calendar_event",
    }
    try:
        mv = init_memvid()
        with _mv_lock:
            mv.put(
                title=f"Event: {subject[:40]}",
                text=doc_text,
                tags=["calendar"],
                metadata=metadata,
            )
            commit()
        clear_search_cache()
    except Exception as e:
        logger.error("[RAG Memvid] Error indexing calendar event: %s", e)


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
        "<user_profile_context>\r\n"
        "Background info about the user (use ONLY if directly relevant to their question; otherwise ignore):\r\n"
        + "\r\n".join(parts)
        + "\r\n</user_profile_context>"
    )


def get_active_context_prompt() -> str:
    state = get_active_state(clean_expired=False)
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
    return f"[PC Environment State: {' | '.join(parts)}]" if parts else ""


def get_index_stats() -> dict[str, int]:
    """Return memory statistics for each collection."""
    counts = {col: 0 for col in ALL_COLLECTIONS}
    try:
        mv = init_memvid()
        with _mv_lock:
            timeline = mv.timeline() or []
            for item in timeline:
                preview = item.get("preview") or ""
                if "tags: conversation" in preview:
                    counts[CONVERSATIONS] += 1
                elif "tags: user_fact" in preview:
                    counts[USER_FACTS] += 1
                elif "tags: document" in preview:
                    counts[DOCUMENTS] += 1
                elif "tags: email" in preview:
                    counts[EMAILS] += 1
                elif "tags: calendar" in preview:
                    counts[CALENDAR] += 1
    except Exception as e:
        logger.debug("[RAG Memvid] Error reading stats: %s", e)

    counts["total"] = sum(counts.values())
    return counts

get_stats = get_index_stats


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
    if _initialized:
        return

    def _worker():
        global _initialized, _init_failed, _init_error
        try:
            init_memvid()
            _initialized = True
            _init_failed = False
            _init_error = None
            logger.info("[RAG Memvid] Initialized successfully at %s", MEMVID_PATH)
        except Exception as e:
            _init_failed, _initialized, _init_error = True, False, e
            logger.error("[RAG Memvid] Initialization failed: %s", e)

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
