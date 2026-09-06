"""
RAG Engine for Amigo Voice Assistant.
ChromaDB vector store + sentence-transformers embeddings.
Semantic long-term memory, contextual search, and profile management.

Collections:
  - conversations: Every conversation turn ever
  - user_facts:    Remembered facts about the user
  - documents:     Indexed local files (PDF/DOCX/TXT/CSV/MD/PPTX/code)
  - emails:        Indexed Outlook emails
  - calendar:      Indexed Outlook calendar events

Lightweight structured config in amigo_profile.json.
"""

import collections
import copy
import datetime
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from typing import Any

# Pre-compiled regex patterns for PDF cleanup & chunking
_RE_PDF_NEWLINES = re.compile(r'(?<=[a-zA-Z0-9])\n(?=[a-zA-Z0-9])')
_RE_PDF_ADJACENT = re.compile(r"([A-Z0-9]{2,})([A-Z][a-z]+)")
_RE_CHUNK_WHITESPACE = re.compile(r"\s+")
_RE_CHUNK_SENTENCES = re.compile(r"(?<=[.!?])\s+")



logger = logging.getLogger("amigo.rag_engine")

# Suppress verbose third-party HTTP request logs during embedding model loading
for _log_name in ("httpx", "httpcore", "sentence_transformers", "transformers", "huggingface_hub", "urllib3"):
    logging.getLogger(_log_name).setLevel(logging.WARNING)


# ── Paths ──────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAG_DATA_DIR = os.path.join(_BASE_DIR, "rag_data")
CHROMA_DIR = os.path.join(RAG_DATA_DIR, "chroma")
PROFILE_FILE = os.path.join(_BASE_DIR, "amigo_profile.json")
FILE_HASHES_PATH = os.path.join(RAG_DATA_DIR, "file_hashes.json")

# ── Constants ──────────────────────────────────────────────────
CONVERSATIONS = "conversations"
USER_FACTS = "user_facts"
DOCUMENTS = "documents"
EMAILS = "emails"
CALENDAR = "calendar"
ALL_COLLECTIONS = [CONVERSATIONS, USER_FACTS, DOCUMENTS, EMAILS, CALENDAR]

SUPPORTED_EXTENSIONS = {
    # Core documents & notes
    ".pdf", ".docx", ".doc", ".txt", ".md",
    # Essential spreadsheets & presentations
    ".xlsx", ".csv", ".pptx",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

MEDIA_STATE_TTL = 1800   # 30 min
APP_STATE_TTL = 1800
SEARCH_STATE_TTL = 900   # 15 min
FILE_STATE_TTL = 1800     # 30 min
SUBJECT_STATE_TTL = 1800  # 30 min

# ── Lazy-loaded globals ────────────────────────────────────────
_chroma_client = None
_embedding_fn = None
_collections: dict = {}
_init_lock = threading.Lock()

_profile_cache: dict | None = None
_profile_lock = threading.Lock()

# In-memory ring buffer of recent conversations (fast access for LLM context).
# Populated from ChromaDB on first access, updated on every add_conversation().
_conversation_buffer: collections.deque | None = None
_BUFFER_MAX = 50


# ═══════════════════════════════════════════════════════════════
#  ChromaDB & Embedding Initialization
# ═══════════════════════════════════════════════════════════════

def _init_chroma() -> None:
    """Initialize ChromaDB persistent client and embedding function."""
    global _chroma_client, _embedding_fn, _collections

    with _init_lock:
        if _chroma_client is not None:
            return

        os.makedirs(CHROMA_DIR, exist_ok=True)

        try:
            import chromadb
            from chromadb.utils import embedding_functions

            # Fast offline-first initialization: prevent HuggingFace SSL/HTTP roundtrips if weights exist locally
            cache_root = os.path.expanduser(os.path.join("~", ".cache", "huggingface", "hub"))
            model_cache_exists = any(
                os.path.exists(os.path.join(cache_root, f"models--sentence-transformers--{EMBEDDING_MODEL.lower()}"))
                for _ in [None]
            ) if os.path.exists(cache_root) else False

            if model_cache_exists:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"

            _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

            try:
                _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=EMBEDDING_MODEL,
                )
            except Exception as first_err:
                # Force offline fallback
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                try:
                    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                        model_name=EMBEDDING_MODEL,
                    )
                except Exception as second_err:
                    # Final attempt online without offline flags
                    os.environ.pop("HF_HUB_OFFLINE", None)
                    os.environ.pop("TRANSFORMERS_OFFLINE", None)
                    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                        model_name=EMBEDDING_MODEL,
                    )

            for name in ALL_COLLECTIONS:
                _collections[name] = _chroma_client.get_or_create_collection(
                    name=name,
                    embedding_function=_embedding_fn,
                    metadata={"hnsw:space": "cosine"},
                )

            logger.info("[RAG] ChromaDB initialized with %d collections in fast mode.", len(_collections))
        except Exception as e:
            logger.error("[RAG] ChromaDB init failed: %s", e)
            raise


def _col(name: str):
    """Get a ChromaDB collection by name, initializing engine if needed."""
    if _chroma_client is None:
        _init_chroma()
    return _collections.get(name)


# ═══════════════════════════════════════════════════════════════
#  Profile Management  (amigo_profile.json)
# ═══════════════════════════════════════════════════════════════

def _default_profile() -> dict:
    return {
        "identity": {"name": "", "role": ""},
        "ui_settings": {},
        "preferences": {

            "favorite_artists": [],
            "favorite_genres": [],
            "favorite_city": "",
            "theme": "dark",
            "preferred_style": "conversational",
            "rag_scan_dirs": [],
            "language": "en-us",
        },
        "active_state": {
            "current_media": None,
            "active_app": {"name": "", "timestamp": None},
            "last_search": {"query": "", "timestamp": None},
            "active_subject": {"name": "", "category": "", "timestamp": None},
            "active_file": {"path": "", "name": "", "timestamp": None},
        },
        "stats": {
            "total_turns": 0,
            "session_count": 0,
            "last_seen": None,
            "clipboard_uses": 0,
            "screen_reads": 0,
            "top_tools": {},
            "rag_indexed_files": 0,
            "rag_indexed_conversations": 0,
        },
    }


def load_profile() -> dict:
    """Load user profile from disk or return defaults."""
    global _profile_cache
    if _profile_cache is not None:
        return _profile_cache

    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                default = _default_profile()
                for section, default_val in default.items():
                    if section not in loaded:
                        loaded[section] = default_val
                    elif isinstance(default_val, dict):
                        for k, v in default_val.items():
                            loaded[section].setdefault(k, v)
                _profile_cache = loaded
                return _profile_cache
        except Exception:
            pass

    _profile_cache = _default_profile()
    return _profile_cache


_profile_queue = queue.Queue()


def _profile_writer_worker():
    """Background worker that persists profile changes without thread spawning thrashing."""
    while True:
        p = _profile_queue.get()
        try:
            # Drain any newer queued updates to write only the newest state
            while not _profile_queue.empty():
                try:
                    p = _profile_queue.get_nowait()
                    _profile_queue.task_done()
                except queue.Empty:
                    break
            with _profile_lock:
                dumped = json.dumps(p, indent=2)
                with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                    f.write(dumped)
        except Exception as e:
            logger.error("[RAG] Error saving profile: %s", e)
        finally:
            _profile_queue.task_done()


threading.Thread(target=_profile_writer_worker, daemon=True, name="ProfileWriter").start()


def save_profile(profile: dict) -> None:
    """Update RAM cache and asynchronously persist profile to disk via background queue."""
    global _profile_cache
    _profile_cache = profile
    try:
        # Snapshot dictionary shallowly to avoid modification during serialization
        _profile_queue.put(copy.deepcopy(profile))
    except Exception:
        _profile_queue.put(dict(profile))



# ── Active State ───────────────────────────────────────────────

def _is_expired(ts_str: str | None, ttl: int) -> bool:
    if not ts_str:
        return True
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(ts_str)).total_seconds() > ttl
    except Exception:
        return True


def get_active_state(clean_expired: bool = True) -> dict:
    """Returns active state slots, auto-cleaning expired entries."""
    state = load_profile().get("active_state", {})
    if not isinstance(state, dict):
        return {}

    if clean_expired:
        media = state.get("current_media")
        if media and isinstance(media, dict) and _is_expired(media.get("timestamp"), MEDIA_STATE_TTL):
            state["current_media"] = None

        app = state.get("active_app")
        if app and isinstance(app, dict) and _is_expired(app.get("timestamp"), APP_STATE_TTL):
            state["active_app"] = {"name": "", "timestamp": None}

        search = state.get("last_search")
        if search and isinstance(search, dict) and _is_expired(search.get("timestamp"), SEARCH_STATE_TTL):
            state["last_search"] = {"query": "", "timestamp": None}

        subject = state.get("active_subject")
        if subject and isinstance(subject, dict) and _is_expired(subject.get("timestamp"), SUBJECT_STATE_TTL):
            state["active_subject"] = {"name": "", "category": "", "timestamp": None}

        file_entry = state.get("active_file")
        if file_entry and isinstance(file_entry, dict) and _is_expired(file_entry.get("timestamp"), FILE_STATE_TTL):
            state["active_file"] = {"path": "", "name": "", "timestamp": None}

    return state


def update_active_state(slot: str, data: dict) -> None:
    """Update an active-state slot in profile."""
    if not slot or not isinstance(data, dict):
        return
    profile = load_profile()
    slot_data = dict(data)
    slot_data["timestamp"] = datetime.datetime.now().isoformat()
    profile.setdefault("active_state", {})[slot] = slot_data
    save_profile(profile)


# ═══════════════════════════════════════════════════════════════
#  Document Text Extraction
# ═══════════════════════════════════════════════════════════════

def extract_text(filepath: str) -> str:
    """Extract text from a supported document (PDF, DOCX, XLSX, PPTX, TXT, MD, CSV)."""
    if not os.path.exists(filepath):
        return ""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return ""
    try:
        if ext == ".pdf":
            return _extract_pdf(filepath)
        elif ext in (".docx", ".doc"):
            return _extract_docx(filepath)
        elif ext == ".xlsx":
            return _extract_xlsx(filepath)
        elif ext == ".pptx":
            return _extract_pptx(filepath)
        elif ext in (".txt", ".md", ".csv"):
            return _extract_plain(filepath)
    except Exception as e:
        logger.debug("[RAG] Extraction error for %s: %s", filepath, e)
    return ""


def _extract_pdf(filepath: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    try:
        reader = PdfReader(filepath)
    except Exception as e:
        logger.debug("[RAG] Failed to read PDF %s: %s", filepath, e)
        return ""

    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
            # If standard extract returns vertical single-character lines (e.g. F\no\nr\nm\na\nt), try layout mode
            if text and text.count("\n") > max(20, len(text) * 0.3):
                try:
                    layout_text = page.extract_text(extraction_mode="layout")
                    if layout_text and len(layout_text.strip()) > 10:
                        text = layout_text
                except Exception:
                    pass
            if text:
                # Fix single character newlines (e.g. F\no\nr\nm\na\nt -> Format)
                cleaned = _RE_PDF_NEWLINES.sub('', text)
                # Separate adjacent alphanumeric codes from titlecase words (e.g. "E6OZGINew" -> "E6OZGI New")
                cleaned = _RE_PDF_ADJACENT.sub(r"\1 \2", cleaned)
                pages.append(cleaned.strip())
        except Exception:
            continue
    return "\n\n".join(pages)




def _extract_docx(filepath: str) -> str:
    try:
        import docx
        doc = docx.Document(filepath)
        parts: list[str] = []
        for p in doc.paragraphs:
            if p.text.strip():
                parts.append(p.text.strip())
        for table in doc.tables:
            for row in table.rows:
                row_cells = [c.text.strip().replace("\n", " ") for c in row.cells if c.text.strip()]
                # Deduplicate merged cells
                seen = set()
                deduped = []
                for cell_t in row_cells:
                    if cell_t not in seen:
                        seen.add(cell_t)
                        deduped.append(cell_t)
                if deduped:
                    parts.append(" | ".join(deduped))
        return "\n\n".join(parts)
    except Exception as e:
        logger.debug("[RAG] Extraction error for docx %s: %s", filepath, e)
        return ""


def _extract_xlsx(filepath: str) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        sheets: list[str] = []
        for sheet in wb.worksheets:
            rows_text: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                non_empty = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if non_empty:
                    rows_text.append(" | ".join(non_empty))
            if rows_text:
                sheets.append(f"[Sheet: {sheet.title}]\n" + "\n".join(rows_text[:300]))
        return "\n\n".join(sheets)
    except Exception as e:
        logger.debug("[RAG] Extraction error for xlsx %s: %s", filepath, e)
        return ""


def _extract_pptx(filepath: str) -> str:
    from pptx import Presentation
    prs = Presentation(filepath)
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        texts.append(t)
    return "\n\n".join(texts)


def _extract_plain(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(MAX_FILE_SIZE)


# ═══════════════════════════════════════════════════════════════
#  Smart Chunking
# ═══════════════════════════════════════════════════════════════

def smart_chunk(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks that respect sentence boundaries."""
    if not text or len(text.strip()) < 20:
        return []

    text = _RE_CHUNK_WHITESPACE.sub(" ", text).strip()
    sentences = _RE_CHUNK_SENTENCES.split(text)


    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > chunk_size and current:
            chunks.append(current.strip())
            # Keep overlap from end of previous chunk
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + " " + sentence
        else:
            current = (current + " " + sentence).strip()

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if len(c) > 20]


# ═══════════════════════════════════════════════════════════════
#  Conversation Memory
# ═══════════════════════════════════════════════════════════════

def _ensure_buffer() -> collections.deque:
    """Lazy-load conversation buffer from ChromaDB on first access."""
    global _conversation_buffer
    if _conversation_buffer is not None:
        return _conversation_buffer

    _conversation_buffer = collections.deque(maxlen=_BUFFER_MAX)

    try:
        col = _col(CONVERSATIONS)
        if col and col.count() > 0:
            fetch_count = min(col.count(), _BUFFER_MAX)
            results = col.get(limit=fetch_count, include=["metadatas", "documents"])
            if results and results.get("metadatas"):
                items = []
                for meta, doc in zip(results["metadatas"], results["documents"]):
                    items.append({
                        "user": meta.get("user_msg", ""),
                        "assistant": meta.get("assistant_msg", ""),
                        "tool": meta.get("tool", "chat"),
                        "timestamp": meta.get("timestamp", ""),
                    })
                items.sort(key=lambda x: x.get("timestamp", ""))
                for item in items[-_BUFFER_MAX:]:
                    _conversation_buffer.append(item)
    except Exception as e:
        logger.debug("[RAG] Buffer load note: %s", e)

    return _conversation_buffer


def add_conversation(
    user_msg: str,
    assistant_msg: str,
    tool: str = "chat",
    clipboard_used: bool = False,
    remember: str = "",
    state_update: dict | None = None,
) -> None:
    """Add a conversation turn to RAG memory. This is the primary memory write."""
    if not user_msg or not user_msg.strip():
        return

    user_msg = re.sub(r"\s+", " ", user_msg).strip()[:1000]
    assistant_msg = re.sub(r"\s+", " ", assistant_msg or "").strip()[:1500]
    timestamp = datetime.datetime.now().isoformat()
    doc_text = f"User: {user_msg}\nAssistant: {assistant_msg}"
    doc_id = f"conv-{uuid.uuid4().hex[:12]}"

    # ── 1. Store in ChromaDB ──
    try:
        col = _col(CONVERSATIONS)
        if col:
            col.add(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "user_msg": user_msg,
                    "assistant_msg": assistant_msg,
                    "tool": tool,
                    "timestamp": timestamp,
                    "type": "conversation",
                }],
            )
    except Exception as e:
        logger.error("[RAG] Error storing conversation: %s", e)

    # ── 2. Update in-memory buffer and conversation cache ──
    turn_item = {
        "user": user_msg,
        "assistant": assistant_msg,
        "tool": tool,
        "timestamp": timestamp,
    }
    buf = _ensure_buffer()
    buf.append(turn_item)
    with _conv_cache_lock:
        if _all_conversations_cache is not None:
            _all_conversations_cache.append(turn_item)

    # ── 3. Active-state updates ──
    if state_update and isinstance(state_update, dict):
        for slot, data in state_update.items():
            update_active_state(slot, data)

    # ── 4. Extract user profile updates ──
    extract_user_profile_updates(user_msg, remember=remember)

    # ── 5. Update profile stats ──
    profile = load_profile()
    stats = profile.setdefault("stats", {})
    stats["total_turns"] = stats.get("total_turns", 0) + 1
    stats["last_seen"] = timestamp
    stats["rag_indexed_conversations"] = stats.get("rag_indexed_conversations", 0) + 1
    if clipboard_used:
        stats["clipboard_uses"] = stats.get("clipboard_uses", 0) + 1
    top_tools = stats.setdefault("top_tools", {})
    top_tools[tool] = top_tools.get(tool, 0) + 1
    save_profile(profile)


def get_recent_conversations(count: int = 6) -> list[dict]:
    """Return the most recent N conversations directly from in-memory buffer."""
    buf = _ensure_buffer()
    return list(buf)[-count:]


_all_conversations_cache: list[dict] | None = None
_conv_cache_lock = threading.Lock()


def invalidate_conversations_cache() -> None:
    """Invalidates the in-memory conversations cache so the next read pulls fresh data from ChromaDB."""
    global _all_conversations_cache
    with _conv_cache_lock:
        _all_conversations_cache = None


def get_all_conversations(limit: int = 200) -> list[dict]:
    """Return stored conversations (cached in memory for high UI throughput)."""
    global _all_conversations_cache
    with _conv_cache_lock:
        if _all_conversations_cache is not None:
            return list(_all_conversations_cache[-limit:])

    try:
        col = _col(CONVERSATIONS)
        if not col or col.count() == 0:
            res = list(_ensure_buffer())
            with _conv_cache_lock:
                _all_conversations_cache = list(res)
            return res

        fetch = min(col.count(), limit)
        results = col.get(limit=fetch, include=["metadatas"])
        if results and results.get("metadatas"):
            items = []
            for meta in results["metadatas"]:
                items.append({
                    "user": meta.get("user_msg", ""),
                    "assistant": meta.get("assistant_msg", ""),
                    "tool": meta.get("tool", "chat"),
                    "timestamp": meta.get("timestamp", ""),
                })
            items.sort(key=lambda x: x.get("timestamp", ""))
            with _conv_cache_lock:
                _all_conversations_cache = list(items)
            return items
    except Exception as e:
        logger.debug("[RAG] get_all_conversations note: %s", e)

    fallback = list(_ensure_buffer())
    with _conv_cache_lock:
        _all_conversations_cache = list(fallback)
    return fallback


def clear_conversations() -> None:
    """Clear all conversation memory (ChromaDB + buffer + memory cache)."""
    global _conversation_buffer, _all_conversations_cache
    _conversation_buffer = collections.deque(maxlen=_BUFFER_MAX)
    with _conv_cache_lock:
        _all_conversations_cache = []

    try:
        import chromadb
        if _chroma_client:
            try:
                _chroma_client.delete_collection(CONVERSATIONS)
            except Exception:
                pass
            _collections[CONVERSATIONS] = _chroma_client.get_or_create_collection(
                name=CONVERSATIONS,
                embedding_function=_embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
    except Exception as e:
        logger.error("[RAG] Error clearing conversations: %s", e)


    # Reset active state
    profile = load_profile()
    profile["active_state"] = {
        "current_media": None,
        "active_app": {"name": "", "timestamp": None},
        "last_search": {"query": "", "timestamp": None},
        "active_subject": {"name": "", "category": "", "timestamp": None},
        "active_file": {"path": "", "name": "", "timestamp": None},
    }
    save_profile(profile)


# ═══════════════════════════════════════════════════════════════
#  User Facts
# ═══════════════════════════════════════════════════════════════

def add_user_fact(fact: str, category: str = "general") -> None:
    """Store a user fact in RAG memory (deduplicated by content hash)."""
    if not fact or len(fact.strip()) < 5:
        return
    doc_id = f"fact-{hashlib.md5(fact.lower().strip().encode()).hexdigest()[:12]}"
    try:
        col = _col(USER_FACTS)
        if col:
            if category == "identity":
                try:
                    col.delete(where={"category": "identity"})
                except Exception:
                    pass
            col.upsert(
                ids=[doc_id],
                documents=[fact.strip()],
                metadatas=[{
                    "category": category,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "type": "user_fact",
                }],
            )
    except Exception as e:
        logger.error("[RAG] Error adding user fact: %s", e)


def get_all_user_facts() -> list[str]:
    """Return all stored user facts."""
    try:
        col = _col(USER_FACTS)
        if col and col.count() > 0:
            results = col.get(limit=100, include=["documents"])
            return results.get("documents", []) if results else []
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════════════════
#  Semantic Search
# ═══════════════════════════════════════════════════════════════

_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SEARCH_CACHE_TTL = 30.0  # 30 seconds TTL for fast repeated queries


def clear_search_cache() -> None:
    """Clear memory search cache when new documents are indexed."""
    _SEARCH_CACHE.clear()


def search(query: str, target_collections: list[str] | None = None, top_k: int = 5) -> list[dict]:
    """Semantic search across ChromaDB collections with sub-millisecond query caching."""
    if not query or not query.strip():
        return []
    if target_collections is None:
        target_collections = [CONVERSATIONS, USER_FACTS, DOCUMENTS]

    cache_key = f"{query.strip().lower()}::{','.join(sorted(target_collections))}::{top_k}"
    now = time.time()
    if cache_key in _SEARCH_CACHE:
        ts, cached_res = _SEARCH_CACHE[cache_key]
        if now - ts < _SEARCH_CACHE_TTL:
            return cached_res

    all_results: list[dict] = []

    for col_name in target_collections:
        try:
            col = _col(col_name)
            if not col or col.count() == 0:
                continue
            results = col.query(
                query_texts=[query],
                n_results=min(top_k, col.count()),
                include=["documents", "metadatas", "distances"],
            )
            if results and results.get("documents") and results["documents"][0]:
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                ):
                    all_results.append({
                        "text": doc,
                        "source": col_name,
                        "score": round(1.0 - dist, 4),
                        "metadata": meta,
                    })
        except Exception as e:
            logger.debug("[RAG] Search error in %s: %s", col_name, e)

    all_results.sort(key=lambda x: x["score"], reverse=True)
    res = all_results[:top_k]

    if len(_SEARCH_CACHE) > 256:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    _SEARCH_CACHE[cache_key] = (now, res)
    return res



def search_documents(query: str, top_k: int = 5) -> list[dict]:
    """Search only indexed documents."""
    return search(query, target_collections=[DOCUMENTS], top_k=top_k)


def search_memory(query: str, top_k: int = 5) -> list[dict]:
    """Search conversation history and user facts."""
    return search(query, target_collections=[CONVERSATIONS, USER_FACTS], top_k=top_k)


def search_emails_rag(query: str, top_k: int = 5) -> list[dict]:
    """Search indexed emails."""
    return search(query, target_collections=[EMAILS], top_k=top_k)


def search_calendar_rag(query: str, top_k: int = 5) -> list[dict]:
    """Search indexed calendar events."""
    return search(query, target_collections=[CALENDAR], top_k=top_k)


def search_all(query: str, top_k: int = 5) -> list[dict]:
    """Search across ALL collections."""
    return search(query, target_collections=ALL_COLLECTIONS, top_k=top_k)


def search_files_by_context(query: str, top_k: int = 10) -> list[dict]:
    """Find files by semantic meaning. Returns deduplicated file paths."""
    results = search_documents(query, top_k=top_k * 3)

    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
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


# ═══════════════════════════════════════════════════════════════
#  RAG Context Builder  (for LLM prompt injection)
# ═══════════════════════════════════════════════════════════════

def build_rag_context(query: str, top_k: int = 5) -> str:
    """Retrieve relevant context from RAG for the LLM prompt.
    Searches across indexed documents and user facts."""
    if not query or not query.strip():
        return ""

    results = search(query, target_collections=[DOCUMENTS, USER_FACTS], top_k=top_k)
    if not results:
        return ""

    # Filter out low-relevance hits (require at least 0.35 similarity score)
    relevant = [r for r in results if r.get("score", 0) >= 0.35]
    if not relevant:
        return ""

    parts: list[str] = []
    for r in relevant:
        src = r.get("source", "")
        text = r.get("text", "")[:1000]
        if src == DOCUMENTS:
            fname = r.get("metadata", {}).get("filename", "document")
            parts.append(f"[From document '{fname}']:\n{text}")
        elif src == USER_FACTS:
            parts.append(f"[Known fact]: {text}")

    return "\n\n".join(parts)


def build_file_context(filepath: str, question: str) -> str:
    """Retrieve relevant chunks or full text from a specific file for Q&A."""
    if not os.path.exists(filepath):
        return ""
    text = extract_text(filepath)
    if not text:
        return ""

    # For short documents (<6000 chars, e.g. invoices, forms, receipts), provide full content for 100% precision
    if len(text) <= 6000:
        return f"[Full content of '{os.path.basename(filepath)}']:\n{text}"

    # For larger documents, query the most relevant chunks from ChromaDB
    try:
        col = _col(DOCUMENTS)
        if col and col.count() > 0:
            results = col.query(
                query_texts=[question],
                n_results=8,
                where={"filepath": filepath},
                include=["documents", "distances"],
            )
            if results and results.get("documents") and results["documents"][0]:
                return f"[Relevant sections from '{os.path.basename(filepath)}']:\n" + "\n\n".join(results["documents"][0])
    except Exception as e:
        logger.debug("[RAG] build_file_context error: %s", e)

    return f"[Content from '{os.path.basename(filepath)}']:\n{text[:4000]}"



def get_file_summary_context(filepath: str) -> str:
    """Extract text from a file for LLM summarization."""
    text = extract_text(filepath)
    if not text:
        return ""
    return f"[Full content of '{os.path.basename(filepath)}']:\n{text[:3000]}"


# ═══════════════════════════════════════════════════════════════
#  Document Indexing
# ═══════════════════════════════════════════════════════════════

def index_document(filepath: str) -> bool:
    """Index a single document into the vector store."""
    if not os.path.exists(filepath):
        return False
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return False
    try:
        fsize = os.path.getsize(filepath)
        if fsize > MAX_FILE_SIZE or fsize == 0:
            return False
    except OSError:
        return False

    text = extract_text(filepath)
    if not text or len(text.strip()) < 20:
        return False

    chunks = smart_chunk(text)
    if not chunks:
        return False

    try:
        col = _col(DOCUMENTS)
        if not col:
            return False

        modified_time = datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
        filename = os.path.basename(filepath)
        file_type = ext.lstrip(".")

        ids, docs, metas = [], [], []
        for i, chunk in enumerate(chunks):
            ids.append(hashlib.md5(f"{filepath}::{i}".encode()).hexdigest())
            docs.append(chunk)
            metas.append({
                "filepath": filepath,
                "filename": filename,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "file_type": file_type,
                "modified_time": modified_time,
                "type": "document",
            })

        col.upsert(ids=ids, documents=docs, metadatas=metas)
        clear_search_cache()
        logger.info("[RAG] Indexed '%s' (%d chunks)", filename, len(chunks))
        return True

    except Exception as e:
        logger.error("[RAG] Indexing error for '%s': %s", filepath, e)
        return False


def index_email(subject: str, sender: str, body_preview: str, date: str,
                is_read: bool = True, folder: str = "Inbox") -> None:
    """Index an email into the vector store."""
    doc_text = f"Subject: {subject}\nFrom: {sender}\nDate: {date}\n{body_preview}"
    doc_id = hashlib.md5(f"{subject}::{sender}::{date}".encode()).hexdigest()
    try:
        col = _col(EMAILS)
        if col:
            col.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "subject": (subject or "")[:200],
                    "sender": (sender or "")[:100],
                    "date": date,
                    "is_read": is_read,
                    "folder": folder,
                    "type": "email",
                }],
            )
    except Exception as e:
        logger.error("[RAG] Error indexing email: %s", e)


def index_calendar_event(subject: str, start: str, end: str,
                         location: str = "", body: str = "") -> None:
    """Index a calendar event into the vector store."""
    doc_text = f"Event: {subject}\nWhen: {start} to {end}"
    if location:
        doc_text += f"\nWhere: {location}"
    if body:
        doc_text += f"\n{body[:300]}"
    doc_id = hashlib.md5(f"{subject}::{start}".encode()).hexdigest()
    try:
        col = _col(CALENDAR)
        if col:
            col.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "subject": (subject or "")[:200],
                    "start": start,
                    "end": end,
                    "location": (location or "")[:200],
                    "type": "calendar_event",
                }],
            )
    except Exception as e:
        logger.error("[RAG] Error indexing calendar event: %s", e)


# ═══════════════════════════════════════════════════════════════
#  User Profile Extraction Helpers
# ═══════════════════════════════════════════════════════════════

def extract_user_profile_updates(user_query: str, remember: str = "") -> None:
    """Extract user identity, preferences, and facts from natural speech."""
    text = (user_query or "").strip()
    if not text:
        return

    profile = load_profile()
    identity = profile.setdefault("identity", {"name": "", "role": ""})
    preferences = profile.setdefault("preferences", {})
    updated = False

    # Name
    if m := re.search(r"\b(?:my name is|call me)\s+([A-Za-z\s]{2,30})", text, re.I):
        name = m.group(1).strip().title()
        if name and name.lower() not in ("amigo", "user", "someone"):
            identity["name"] = name
            add_user_fact(f"User's name is {name}", category="identity")
            updated = True

    # Favorite Artist
    if m := re.search(r"\b(?:my (?:favorite|favourite) artist is|i love listening to)\s+([A-Za-z0-9\s]{2,40})", text, re.I):
        artist = m.group(1).strip().title()
        favs = preferences.setdefault("favorite_artists", [])
        if artist not in favs:
            favs.append(artist)
            preferences["favorite_artists"] = favs[-10:]
            add_user_fact(f"Favorite artist: {artist}", category="preference")
            updated = True

    # Favorite City
    if m := re.search(r"\b(?:i live in|my city is)\s+([A-Za-z\s]{2,40})", text, re.I):
        city = m.group(1).strip().title()
        preferences["favorite_city"] = city
        add_user_fact(f"User lives in {city}", category="preference")
        updated = True

    # Custom facts via "remember" field
    if remember and len(remember.strip()) > 5:
        add_user_fact(remember.strip(), category="custom")
        updated = True

    if updated:
        save_profile(profile)


def get_user_profile_prompt() -> str:
    """Format user profile for LLM prompt injection."""
    profile = load_profile()
    parts: list[str] = []
    user_name = profile.get("identity", {}).get("name")
    if user_name:
        parts.append(f"The user's name is {user_name}. You know their name. Do NOT say 'Hello {user_name}!' repeatedly on every turn in an ongoing conversation; speak naturally as a companion.")
    if artists := profile.get("preferences", {}).get("favorite_artists"):
        parts.append(f"User's favorite artists: {', '.join(artists[:3])}.")
    if city := profile.get("preferences", {}).get("favorite_city"):
        parts.append(f"User lives in: {city}.")

    # Pull latest user facts from RAG (skip redundant name facts and image dumps)
    facts = get_all_user_facts()
    for f in facts[-5:]:
        if f and not (user_name and f.lower().startswith("user's name is")) and not f.startswith("Uploaded image"):
            parts.append(f"Remembered fact: {f}")

    return "\n".join(parts) if parts else ""



def get_active_context_prompt() -> str:
    """Format active state for LLM prompt injection."""
    state = get_active_state(clean_expired=True)
    parts: list[str] = []

    media = state.get("current_media")
    if media and isinstance(media, dict):
        title = media.get("title") or media.get("query")
        if title:
            parts.append(f"Playing '{title}' on {media.get('platform', 'YouTube')}")

    app = state.get("active_app")
    if app and isinstance(app, dict) and app.get("name"):
        parts.append(f"Active App: {app['name']}")

    srch = state.get("last_search")
    if srch and isinstance(srch, dict) and srch.get("query"):
        parts.append(f"Recent Search: '{srch['query']}'")

    return f"[Active State: {' | '.join(parts)}]" if parts else ""


# ═══════════════════════════════════════════════════════════════
#  Index Statistics
# ═══════════════════════════════════════════════════════════════

def get_index_stats() -> dict:
    """Return per-collection document counts and total."""
    stats: dict[str, Any] = {}
    for name in ALL_COLLECTIONS:
        try:
            col = _col(name)
            stats[name] = col.count() if col else 0
        except Exception:
            stats[name] = 0
    stats["total"] = sum(stats.values())
    return stats


# ═══════════════════════════════════════════════════════════════
#  Unified Memory & State Interface
# ═══════════════════════════════════════════════════════════════

def load_memory() -> dict:
    """Returns a memory dict shaped for UI and agent consumers.
    Backed by ChromaDB vector collections + amigo_profile.json."""
    profile = load_profile()
    conversations = get_all_conversations(limit=200)
    return {
        "active_state": profile.get("active_state", {}),
        "ui_settings": profile.get("ui_settings", {}),
        "conversations": conversations,
        "user_profile": {
            "identity": profile.get("identity", {}),
            "preferences": profile.get("preferences", {}),
            "custom_facts": get_all_user_facts(),
        },
        "interaction_style": profile.get("stats", {}),
    }


def save_memory(memory: dict) -> None:
    """Saves profile and user facts portions of a memory dict."""
    profile = load_profile()
    if "ui_settings" in memory and isinstance(memory["ui_settings"], dict):
        profile["ui_settings"] = memory["ui_settings"]
    if "user_profile" in memory:
        up = memory["user_profile"]
        if isinstance(up, dict):
            if "identity" in up:
                profile["identity"] = up["identity"]
            if "preferences" in up:
                profile["preferences"] = up["preferences"]
            if "custom_facts" in up and isinstance(up["custom_facts"], list):
                for fact in up["custom_facts"]:
                    if isinstance(fact, str) and len(fact.strip()) >= 3:
                        add_user_fact(fact.strip(), category="custom")
    save_profile(profile)



# ═══════════════════════════════════════════════════════════════
#  Engine Initialization
# ═══════════════════════════════════════════════════════════════

_initialized = False


def init_rag(background: bool = True) -> None:
    """Initialize RAG engine asynchronously in the background so startup is instant."""
    global _initialized
    if _initialized:
        return

    def _worker():
        global _initialized
        try:
            _init_chroma()
            _initialized = True
            logger.info("[RAG] Engine ready in background. %s", get_index_stats())
        except Exception as e:
            logger.warning("[RAG] Background init note: %s", e)

    if background:
        t = threading.Thread(target=_worker, daemon=True, name="RAG-Init-Thread")
        t.start()
    else:
        _worker()
