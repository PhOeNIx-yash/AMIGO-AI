"""
RAG Engine for Amigo Voice Assistant.
ChromaDB vector store + sentence-transformers embeddings.
Replaces the old JSON-based memory with unlimited, semantically-searchable long-term memory.

Collections:
  - conversations: Every conversation turn ever
  - user_facts:    Remembered facts about the user
  - documents:     Indexed local files (PDF/DOCX/TXT/CSV/MD/PPTX/code)
  - emails:        Indexed Outlook emails
  - calendar:      Indexed Outlook calendar events

Lightweight structured config in amigo_profile.json.
"""

import collections
import datetime
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any


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
    ".pdf", ".docx", ".doc", ".txt", ".md", ".csv",
    ".pptx", ".py", ".json", ".log", ".html", ".xml",
    ".js", ".ts", ".css", ".yaml", ".yml", ".ini", ".cfg",
    ".bat", ".ps1", ".sh", ".c", ".cpp", ".h", ".java",
    ".rs", ".go", ".rb", ".php", ".sql", ".r",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

MEDIA_STATE_TTL = 1800   # 30 min
APP_STATE_TTL = 1800
SEARCH_STATE_TTL = 900   # 15 min

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

            _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
            _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL,
            )

            for name in ALL_COLLECTIONS:
                _collections[name] = _chroma_client.get_or_create_collection(
                    name=name,
                    embedding_function=_embedding_fn,
                    metadata={"hnsw:space": "cosine"},
                )

            logger.info("[RAG] ChromaDB initialized with %d collections.", len(_collections))
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


def save_profile(profile: dict) -> None:
    """Update RAM cache and asynchronously persist profile to disk."""
    global _profile_cache
    _profile_cache = profile

    def _write():
        with _profile_lock:
            try:
                with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                    json.dump(profile, f, indent=2)
            except Exception as e:
                logger.error("[RAG] Error saving profile: %s", e)

    threading.Thread(target=_write, daemon=True).start()


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
    """Extract text from a file. Supports PDF, DOCX, PPTX, TXT, CSV, MD, code."""
    if not os.path.exists(filepath):
        return ""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(filepath)
        elif ext in (".docx", ".doc"):
            return _extract_docx(filepath)
        elif ext == ".pptx":
            return _extract_pptx(filepath)
        elif ext in SUPPORTED_EXTENSIONS:
            return _extract_plain(filepath)
    except Exception as e:
        logger.debug("[RAG] Extraction error for %s: %s", filepath, e)
    return ""


def _extract_pdf(filepath: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    reader = PdfReader(filepath)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            # Separate adjacent alphanumeric codes from titlecase words (e.g. "E6OZGINew" -> "E6OZGI New")
            cleaned = re.sub(r"([A-Z0-9]{2,})([A-Z][a-z]+)", r"\1 \2", text)
            pages.append(cleaned.strip())
    return "\n\n".join(pages)




def _extract_docx(filepath: str) -> str:
    import docx
    doc = docx.Document(filepath)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


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

    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)

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

    user_msg = re.sub(r"\s+", " ", user_msg).strip()[:500]
    assistant_msg = re.sub(r"\s+", " ", assistant_msg or "").strip()[:500]
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
                    "user_msg": user_msg[:250],
                    "assistant_msg": assistant_msg[:250],
                    "tool": tool,
                    "timestamp": timestamp,
                    "type": "conversation",
                }],
            )
    except Exception as e:
        logger.error("[RAG] Error storing conversation: %s", e)

    # ── 2. Update in-memory buffer ──
    buf = _ensure_buffer()
    buf.append({
        "user": user_msg,
        "assistant": assistant_msg,
        "tool": tool,
        "timestamp": timestamp,
    })

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
    """Return the most recent N conversations from in-memory buffer."""
    buf = _ensure_buffer()
    return list(buf)[-count:]


def get_all_conversations(limit: int = 200) -> list[dict]:
    """Return all stored conversations (for history UI). Falls through to ChromaDB."""
    try:
        col = _col(CONVERSATIONS)
        if not col or col.count() == 0:
            return list(_ensure_buffer())

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
            return items
    except Exception as e:
        logger.debug("[RAG] get_all_conversations note: %s", e)
    return list(_ensure_buffer())


def clear_conversations() -> None:
    """Clear all conversation memory (ChromaDB + buffer)."""
    global _conversation_buffer
    _conversation_buffer = collections.deque(maxlen=_BUFFER_MAX)

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
        "active_subject": {"name": "", "timestamp": None},
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

def build_rag_context(query: str, top_k: int = 4) -> str:
    """Retrieve relevant context from RAG for the LLM prompt.
    Searches across conversations, user facts, and documents."""
    if not query or not query.strip():
        return ""

    results = search(query, target_collections=[CONVERSATIONS, USER_FACTS, DOCUMENTS], top_k=top_k)
    if not results:
        return ""

    # Filter out low-relevance hits
    relevant = [r for r in results if r["score"] > 0.25]
    if not relevant:
        return ""

    parts: list[str] = []
    for r in relevant:
        src = r["source"]
        text = r["text"][:800]
        if src == DOCUMENTS:
            fname = r.get("metadata", {}).get("filename", "unknown")
            parts.append(f"[From file '{fname}']: {text}")
        elif src == CONVERSATIONS:
            parts.append(f"[Past conversation]: {text}")
        elif src == USER_FACTS:
            parts.append(f"[Known fact]: {text}")

    return "\n".join(parts)


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
    if name := profile.get("identity", {}).get("name"):
        parts.append(f"The user's name is {name}. When the user asks for their name, tell them their name is {name}.")
    if artists := profile.get("preferences", {}).get("favorite_artists"):
        parts.append(f"User's favorite artists: {', '.join(artists[:3])}.")
    if city := profile.get("preferences", {}).get("favorite_city"):
        parts.append(f"User lives in: {city}.")

    # Pull latest user facts from RAG
    facts = get_all_user_facts()
    for f in facts[-5:]:
        if f:
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
#  Compatibility Layer  (drop-in for old ai.py consumers)
# ═══════════════════════════════════════════════════════════════

def load_memory() -> dict:
    """Backward-compatible: returns a dict shaped like the old amigo_memory.json.
    Used by ui_server /api/history and amigo main.py."""
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
    """Backward-compatible: saves profile portion of a memory dict."""
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
    save_profile(profile)



# ═══════════════════════════════════════════════════════════════
#  Engine Initialization
# ═══════════════════════════════════════════════════════════════

_initialized = False


def init_rag() -> None:
    """Initialize RAG engine."""
    global _initialized
    if _initialized:
        return

    try:
        _init_chroma()
        _initialized = True
        logger.info("[RAG] Engine ready. %s", get_index_stats())
    except Exception as e:
        logger.error("[RAG] Init failed: %s", e)
