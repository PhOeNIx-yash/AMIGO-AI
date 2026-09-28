"""
Enhanced RAG Engine for Amigo Voice Assistant.
Improvements over v1:
- Better embedding model (bge-large-en-v1.5)
- Semantic chunking with recursive splitting
- Hybrid search (semantic + BM25 keyword)
- Cross-encoder reranking
- Query expansion/rewriting
- Conversation summarization
- Improved document extraction
- Async search with better caching
- Importance scoring for memories
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
from typing import Any, Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

# Pre-compiled regex patterns
_RE_PDF_NEWLINES = re.compile(r'(?<=[a-zA-Z0-9])\n(?=[a-zA-Z0-9])')
_RE_PDF_ADJACENT = re.compile(r"([A-Z0-9]{2,})([A-Z][a-z]+)")
_RE_CHUNK_WHITESPACE = re.compile(r"\s+")
_RE_CHUNK_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_RE_CODE_BLOCKS = re.compile(r"```[\s\S]*?```")
_RE_MARKDOWN_HEADERS = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)

logger = logging.getLogger("amigo.rag_engine_v2")

# Suppress verbose third-party logs
for _log_name in ("httpx", "httpcore", "sentence_transformers", "transformers", "huggingface_hub", "urllib3", "chromadb", "pdfminer", "pdfminer.pdffont"):
    logging.getLogger(_log_name).setLevel(logging.WARNING)


# ── Paths ──────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAG_DATA_DIR = os.path.join(_BASE_DIR, "rag_data")
CHROMA_DIR = os.path.join(RAG_DATA_DIR, "chroma")
PROFILE_FILE = os.path.join(_BASE_DIR, "amigo_profile.json")
FILE_HASHES_PATH = os.path.join(RAG_DATA_DIR, "file_hashes.json")
BM25_INDEX_PATH = os.path.join(RAG_DATA_DIR, "bm25_index.pkl")

# ── Constants ──────────────────────────────────────────────────
CONVERSATIONS = "conversations"
USER_FACTS = "user_facts"
DOCUMENTS = "documents"
EMAILS = "emails"
CALENDAR = "calendar"
ALL_COLLECTIONS = [CONVERSATIONS, USER_FACTS, DOCUMENTS, EMAILS, CALENDAR]

SUPPORTED_EXTENSIONS = {
    # Essential document types only
    ".pdf",           # PDF documents
    ".docx", ".doc",  # Word documents
    ".txt",           # Notepad/text files
    ".xlsx", ".csv",  # Excel/spreadsheet files
    ".pptx",          # PowerPoint presentations
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE = 512
CHUNK_OVERLAP = 128
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"  # 1024 dim, better quality
RERANKER_MODEL = "BAAI/bge-reranker-base"  # Cross-encoder for reranking

MEDIA_STATE_TTL = 1800
APP_STATE_TTL = 1800
SEARCH_STATE_TTL = 900
FILE_STATE_TTL = 1800
SUBJECT_STATE_TTL = 1800

# ── Lazy-loaded globals ────────────────────────────────────────
_chroma_client = None
_embedding_fn = None
_reranker = None
_bm25_index = None
_bm25_doc_map = None
_collections: dict = {}
_init_lock = threading.Lock()

_profile_cache: dict | None = None
_profile_lock = threading.Lock()

_conversation_buffer: collections.deque | None = None
_BUFFER_MAX = 100  # Increased from 50

_all_conversations_cache: list[dict] | None = None
_conv_cache_lock = threading.Lock()

# Thread pool for async operations
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="RAG-Async")

# Search cache with TTL
_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SEARCH_CACHE_TTL = 60.0  # 60 seconds

# BM25 index update queue
_bm25_update_queue: queue.Queue = queue.Queue()


@dataclass
class SearchResult:
    """Enhanced search result with metadata."""
    text: str
    source: str
    score: float
    metadata: dict
    rerank_score: float = 0.0
    keyword_score: float = 0.0
    importance: float = 1.0


@dataclass
class DocumentChunk:
    """Represents a document chunk with metadata."""
    text: str
    filepath: str
    filename: str
    chunk_index: int
    total_chunks: int
    file_type: str
    modified_time: str
    embedding: Optional[list] = None


# ═══════════════════════════════════════════════════════════════
#  ChromaDB & Embedding Initialization
# ═══════════════════════════════════════════════════════════════

def _init_chroma() -> None:
    """Initialize ChromaDB persistent client and embedding function."""
    global _chroma_client, _embedding_fn, _collections, _reranker
    
    with _init_lock:
        if _chroma_client is not None:
            return

        os.makedirs(CHROMA_DIR, exist_ok=True)

        try:
            import chromadb
            from chromadb.utils import embedding_functions

            # Fast offline-first initialization
            cache_root = os.path.expanduser(os.path.join("~", ".cache", "huggingface", "hub"))
            model_cache_exists = any(
                os.path.exists(os.path.join(cache_root, f"models--sentence-transformers--{EMBEDDING_MODEL.replace('/', '--')}"))
                for _ in [None]
            ) if os.path.exists(cache_root) else False

            if model_cache_exists:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"

            _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

            # Use better embedding model
            try:
                _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=EMBEDDING_MODEL,
                )
            except Exception as first_err:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                try:
                    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                        model_name=EMBEDDING_MODEL,
                    )
                except Exception as second_err:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                    os.environ.pop("TRANSFORMERS_OFFLINE", None)
                    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                        model_name=EMBEDDING_MODEL,
                    )

            # Check if collections need to be recreated due to embedding dimension change
            _recreate_collections_if_needed()

            for name in ALL_COLLECTIONS:
                _collections[name] = _chroma_client.get_or_create_collection(
                    name=name,
                    embedding_function=_embedding_fn,
                    metadata={"hnsw:space": "cosine"},
                )

            # Initialize cross-encoder reranker
            _init_reranker()
            
            # Initialize BM25 index
            _init_bm25_index()

            logger.info("[RAG v2] ChromaDB initialized with %d collections using %s", len(_collections), EMBEDDING_MODEL)
        except Exception as e:
            logger.error("[RAG v2] ChromaDB init failed: %s", e)
            raise


def _recreate_collections_if_needed() -> None:
    """Check if existing collections have different embedding dimensions and recreate if needed."""
    try:
        # Get the expected dimension from the new embedding model
        test_embedding = _embedding_fn(["test"])
        expected_dim = len(test_embedding[0]) if test_embedding else 1024
        
        for name in ALL_COLLECTIONS:
            try:
                existing_col = _chroma_client.get_collection(name=name)
                if existing_col:
                    # Check the dimension of existing embeddings
                    count = existing_col.count()
                    if count > 0:
                        # Get a sample embedding to check dimension using get with include=["embeddings"]
                        sample = existing_col.get(limit=1, include=["embeddings"])
                        if sample and 'embeddings' in sample and sample['embeddings'] is not None:
                            emb = sample['embeddings']
                            existing_dim = None
                            if isinstance(emb, list) and len(emb) > 0:
                                first_emb = emb[0]
                                if isinstance(first_emb, list):
                                    existing_dim = len(first_emb)
                                elif hasattr(first_emb, 'shape'):  # numpy array
                                    existing_dim = first_emb.shape[0]
                            elif hasattr(emb, 'shape'):  # numpy array directly
                                existing_dim = emb.shape[1] if len(emb.shape) > 1 else emb.shape[0]
                            
                            if existing_dim is not None and existing_dim != expected_dim:
                                logger.info("[RAG v2] Collection '%s' has dimension %d, expected %d. Deleting...", 
                                           name, existing_dim, expected_dim)
                                _chroma_client.delete_collection(name=name)
            except Exception as e:
                logger.debug("[RAG v2] Could not check collection '%s': %s", name, e)
                # Collection doesn't exist or other error, will be created fresh
                pass
    except Exception as e:
        logger.warning("[RAG v2] Could not check collection dimensions: %s", e)


def _init_reranker() -> None:
    """Initialize cross-encoder reranker."""
    global _reranker
    try:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL, max_length=512)
        logger.info("[RAG v2] Cross-encoder reranker initialized: %s", RERANKER_MODEL)
    except Exception as e:
        logger.warning("[RAG v2] Reranker init failed (will use semantic only): %s", e)
        _reranker = None


def _init_bm25_index() -> None:
    """Initialize BM25 index for keyword search."""
    global _bm25_index, _bm25_doc_map
    try:
        import pickle
        if os.path.exists(BM25_INDEX_PATH):
            with open(BM25_INDEX_PATH, 'rb') as f:
                data = pickle.load(f)
                _bm25_index = data.get('index')
                _bm25_doc_map = data.get('doc_map', {})
            logger.info("[RAG v2] BM25 index loaded with %d documents", len(_bm25_doc_map))
        else:
            _bm25_index = None
            _bm25_doc_map = {}
    except Exception as e:
        logger.warning("[RAG v2] BM25 index load failed: %s", e)
        _bm25_index = None
        _bm25_doc_map = {}


def _save_bm25_index() -> None:
    """Save BM25 index to disk."""
    try:
        import pickle
        with open(BM25_INDEX_PATH, 'wb') as f:
            pickle.dump({
                'index': _bm25_index,
                'doc_map': _bm25_doc_map
            }, f)
    except Exception as e:
        logger.error("[RAG v2] BM25 index save failed: %s", e)


def _rebuild_bm25_index() -> None:
    """Rebuild BM25 index from all documents."""
    global _bm25_index, _bm25_doc_map
    try:
        from rank_bm25 import BM25Okapi
        
        col = _col(DOCUMENTS)
        if not col or col.count() == 0:
            _bm25_index = None
            _bm25_doc_map = {}
            return

        results = col.get(include=["documents", "metadatas"])
        if not results or not results.get("documents"):
            return

        corpus = []
        doc_map = {}
        for i, (doc, meta) in enumerate(zip(results["documents"], results["metadatas"])):
            tokens = doc.lower().split()
            corpus.append(tokens)
            doc_map[i] = {
                'text': doc,
                'metadata': meta,
                'id': results["ids"][i] if "ids" in results else str(i)
            }

        _bm25_index = BM25Okapi(corpus)
        _bm25_doc_map = doc_map
        _save_bm25_index()
        logger.info("[RAG v2] BM25 index rebuilt with %d documents", len(corpus))
    except Exception as e:
        logger.error("[RAG v2] BM25 rebuild failed: %s", e)


def _bm25_update_worker():
    """Background worker for BM25 index updates."""
    while True:
        try:
            _bm25_update_queue.get()
            _rebuild_bm25_index()
        except Exception as e:
            logger.error("[RAG v2] BM25 update worker error: %s", e)
        finally:
            _bm25_update_queue.task_done()


threading.Thread(target=_bm25_update_worker, daemon=True, name="BM25-Updater").start()


def _col(name: str):
    """Get a ChromaDB collection by name, initializing engine if needed."""
    if _chroma_client is None:
        _init_chroma()
    return _collections.get(name)


# ═══════════════════════════════════════════════════════════════
#  Profile Management
# ═══════════════════════════════════════════════════════════════

def _default_profile() -> dict:
    return {
        "identity": {"name": "", "role": ""},
        "ui_settings": {},
        "preferences": {},
        "stats": {"total_interactions": 0, "last_active": None},
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
    """Background worker that persists profile changes."""
    while True:
        p = _profile_queue.get()
        try:
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
            logger.error("[RAG v2] Error saving profile: %s", e)
        finally:
            _profile_queue.task_done()


threading.Thread(target=_profile_writer_worker, daemon=True, name="ProfileWriter").start()


_conv_write_queue: queue.Queue = queue.Queue()


def _conv_writer_worker():
    """Background worker that stores conversation embedding in ChromaDB asynchronously."""
    while True:
        item = _conv_write_queue.get()
        try:
            col = _col(CONVERSATIONS)
            if col:
                col.add(
                    ids=[item["doc_id"]],
                    documents=[item["doc_text"]],
                    metadatas=[item["metadata"]],
                )
        except Exception as e:
            logger.error("[RAG v2] Async error storing conversation: %s", e)
        finally:
            _conv_write_queue.task_done()


threading.Thread(target=_conv_writer_worker, daemon=True, name="ConvWriter").start()


def save_profile(profile: dict) -> None:
    """Update RAM cache and asynchronously persist profile to disk."""
    global _profile_cache
    _profile_cache = profile
    try:
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


_ACTIVE_STATE: dict = {
    "current_media": None,
    "active_app": {"name": "", "timestamp": None},
    "last_search": {"query": "", "timestamp": None},
    "active_subject": {"name": "", "category": "", "timestamp": None},
    "active_file": {"path": "", "name": "", "timestamp": None},
}


def get_active_state(clean_expired: bool = True) -> dict:
    """Returns active state slots from in-memory cache, auto-cleaning expired entries."""
    state = _ACTIVE_STATE
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
    """Update an active-state slot in memory."""
    if not slot or not isinstance(data, dict):
        return
    slot_data = dict(data)
    slot_data["timestamp"] = datetime.datetime.now().isoformat()
    _ACTIVE_STATE[slot] = slot_data


# ═══════════════════════════════════════════════════════════════
#  Enhanced Document Text Extraction
# ═══════════════════════════════════════════════════════════════

_extracted_text_cache: dict[tuple[str, float], str] = {}
_extracted_text_lock = threading.Lock()


def extract_text(filepath: str) -> str:
    """Extract text from a supported document with mtime-based RAM caching."""
    if not os.path.exists(filepath):
        return ""

    cache_key = None
    try:
        mtime = os.path.getmtime(filepath)
        cache_key = (os.path.normpath(filepath), mtime)
        with _extracted_text_lock:
            if cache_key in _extracted_text_cache:
                return _extracted_text_cache[cache_key]
    except Exception:
        pass

    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return ""
    extracted = ""
    try:
        if ext == ".pdf":
            extracted = _extract_pdf_enhanced(filepath)
        elif ext in (".docx", ".doc"):
            extracted = _extract_docx_enhanced(filepath)
        elif ext == ".xlsx":
            extracted = _extract_xlsx_enhanced(filepath)
        elif ext == ".pptx":
            extracted = _extract_pptx_enhanced(filepath)
        elif ext in (".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", 
                     ".sql", ".sh", ".bat", ".ps1", ".html", ".htm", ".xml", ".csv"):
            extracted = _extract_plain(filepath)
    except Exception as e:
        logger.debug("[RAG v2] Extraction error for %s: %s", filepath, e)

    if cache_key and extracted:
        with _extracted_text_lock:
            if len(_extracted_text_cache) > 200:
                _extracted_text_cache.clear()
            _extracted_text_cache[cache_key] = extracted

    return extracted


def _extract_pdf_enhanced(filepath: str) -> str:
    """Enhanced PDF extraction with layout awareness and table detection."""
    try:
        import pdfplumber
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        return _extract_pdf_fallback(filepath)

    pages = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                # Extract text with layout preservation
                text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                
                # Extract tables
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        table_text = "\n".join([" | ".join([cell or "" for cell in row]) for row in table])
                        text += f"\n\n[Table]\n{table_text}\n"
                
                if text:
                    cleaned = _RE_PDF_NEWLINES.sub('', text)
                    cleaned = _RE_PDF_ADJACENT.sub(r"\1 \2", cleaned)
                    pages.append(cleaned.strip())
    except Exception as e:
        logger.debug("[RAG v2] pdfplumber error for %s: %s", filepath, e)
        return _extract_pdf_fallback(filepath)

    return "\n\n".join(pages)


def _extract_pdf_fallback(filepath: str) -> str:
    """Fallback PDF extraction using pypdf/PyPDF2."""
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    try:
        reader = PdfReader(filepath)
    except Exception as e:
        logger.debug("[RAG v2] Failed to read PDF %s: %s", filepath, e)
        return ""

    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
            if text and text.count("\n") > max(20, len(text) * 0.3):
                try:
                    layout_text = page.extract_text(extraction_mode="layout")
                    if layout_text and len(layout_text.strip()) > 10:
                        text = layout_text
                except Exception:
                    pass
            if text:
                cleaned = _RE_PDF_NEWLINES.sub('', text)
                cleaned = _RE_PDF_ADJACENT.sub(r"\1 \2", cleaned)
                pages.append(cleaned.strip())
        except Exception:
            continue
    return "\n\n".join(pages)


def _extract_docx_enhanced(filepath: str) -> str:
    """Enhanced DOCX extraction with table and structure preservation."""
    try:
        import docx
        doc = docx.Document(filepath)
        parts: list[str] = []
        
        for p in doc.paragraphs:
            if p.text.strip():
                # Preserve heading structure
                if p.style.name.startswith('Heading'):
                    parts.append(f"\n## {p.text.strip()}\n")
                else:
                    parts.append(p.text.strip())
        
        for table in doc.tables:
            for row in table.rows:
                row_cells = [c.text.strip().replace("\n", " ") for c in row.cells if c.text.strip()]
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
        logger.debug("[RAG v2] Extraction error for docx %s: %s", filepath, e)
        return ""


def _extract_xlsx_enhanced(filepath: str) -> str:
    """Enhanced XLSX extraction with sheet names and formatting."""
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
                sheets.append(f"[Sheet: {sheet.title}]\n" + "\n".join(rows_text[:500]))
        return "\n\n".join(sheets)
    except Exception as e:
        logger.debug("[RAG v2] Extraction error for xlsx %s: %s", filepath, e)
        return ""


def _extract_pptx_enhanced(filepath: str) -> str:
    """Enhanced PPTX extraction with slide structure."""
    try:
        from pptx import Presentation
        prs = Presentation(filepath)
        texts = []
        for i, slide in enumerate(prs.slides):
            slide_texts = [f"[Slide {i+1}]"]
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t:
                            slide_texts.append(t)
            if len(slide_texts) > 1:
                texts.append("\n".join(slide_texts))
        return "\n\n".join(texts)
    except Exception as e:
        logger.debug("[RAG v2] Extraction error for pptx %s: %s", filepath, e)
        return ""


def _extract_plain(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(MAX_FILE_SIZE)


# ═══════════════════════════════════════════════════════════════
#  Semantic Chunking (Recursive with Structure Awareness)
# ═══════════════════════════════════════════════════════════════

def semantic_chunk(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Recursive semantic chunking that respects document structure.
    Splits by headers, then paragraphs, then sentences.
    """
    if not text or len(text.strip()) < 50:
        return []

    # First, try to split by markdown headers (before whitespace normalization)
    header_splits = _RE_MARKDOWN_HEADERS.split(text)
    if len(header_splits) > 1:
        # Has markdown headers, split by them
        chunks = []
        for section in header_splits:
            if not section.strip():
                continue
            # Normalize whitespace within each section
            section = _RE_CHUNK_WHITESPACE.sub(" ", section).strip()
            section_chunks = _chunk_by_paragraphs(section, chunk_size, overlap)
            chunks.extend(section_chunks)
        return [c for c in chunks if len(c) > 30]
    
    # Otherwise, normalize whitespace and split by paragraphs then sentences
    text = _RE_CHUNK_WHITESPACE.sub(" ", text).strip()
    return _chunk_by_paragraphs(text, chunk_size, overlap)


def _chunk_by_paragraphs(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text by paragraphs, then sentences."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        if len(current) + len(para) > chunk_size and current:
            chunks.append(current.strip())
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + "\n\n" + para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    
    if current.strip():
        chunks.append(current.strip())
    
    # If chunks are still too large, split by sentences
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= chunk_size:
            final_chunks.append(chunk)
        else:
            final_chunks.extend(_chunk_by_sentences(chunk, chunk_size, overlap))
    
    return final_chunks


def _chunk_by_sentences(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text by sentences with overlap."""
    sentences = _RE_CHUNK_SENTENCES.split(text)
    chunks = []
    current = ""
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
            
        if len(current) + len(sentence) > chunk_size and current:
            chunks.append(current.strip())
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + " " + sentence
        else:
            current = (current + " " + sentence).strip()
    
    if current.strip():
        chunks.append(current.strip())
    
    return [c for c in chunks if len(c) > 30]


# ═══════════════════════════════════════════════════════════════
#  Conversation Memory with Summarization
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
                        "importance": meta.get("importance", 1.0),
                    })
                items.sort(key=lambda x: x.get("timestamp", ""))
                for item in items[-_BUFFER_MAX:]:
                    _conversation_buffer.append(item)
    except Exception as e:
        logger.debug("[RAG v2] Buffer load note: %s", e)

    return _conversation_buffer


def add_conversation(
    user_msg: str,
    assistant_msg: str,
    tool: str = "chat",
    clipboard_used: bool = False,
    remember: str = "",
    state_update: dict | None = None,
    importance: float = 1.0,
) -> None:
    """Add a conversation turn to RAG memory with importance scoring."""
    if not user_msg or not user_msg.strip():
        return

    user_msg = re.sub(r"\s+", " ", user_msg).strip()[:1000]
    assistant_msg = re.sub(r"\s+", " ", assistant_msg or "").strip()[:1500]
    timestamp = datetime.datetime.now().isoformat()
    doc_text = f"User: {user_msg}\nAssistant: {assistant_msg}"
    doc_id = f"conv-{uuid.uuid4().hex[:12]}"

    # Calculate importance based on content
    importance = _calculate_importance(user_msg, assistant_msg, tool, importance)

    # Queue ChromaDB storage asynchronously
    try:
        _conv_write_queue.put({
            "doc_id": doc_id,
            "doc_text": doc_text,
            "metadata": {
                "user_msg": user_msg,
                "assistant_msg": assistant_msg,
                "tool": tool,
                "timestamp": timestamp,
                "type": "conversation",
                "importance": importance,
            },
        })
    except Exception as e:
        logger.error("[RAG v2] Error queueing conversation: %s", e)

    # Update in-memory buffer
    turn_item = {
        "user": user_msg,
        "assistant": assistant_msg,
        "tool": tool,
        "timestamp": timestamp,
        "importance": importance,
    }
    buf = _ensure_buffer()
    buf.append(turn_item)
    with _conv_cache_lock:
        if _all_conversations_cache is not None:
            _all_conversations_cache.append(turn_item)

    # Active-state updates
    if state_update and isinstance(state_update, dict):
        for slot, data in state_update.items():
            update_active_state(slot, data)

    # Extract user profile updates
    extract_user_profile_updates(user_msg, remember=remember)


def _calculate_importance(user_msg: str, assistant_msg: str, tool: str, base_importance: float) -> float:
    """Calculate importance score for a conversation turn."""
    importance = base_importance
    
    # Tool-based importance
    tool_importance = {
        "chat": 0.5,
        "web_search": 0.7,
        "document_qa": 0.8,
        "memory_recall": 0.9,
        "find_file": 0.6,
        "set_reminder": 0.8,
        "get_weather": 0.4,
        "play_youtube": 0.3,
    }
    importance = max(importance, tool_importance.get(tool, 0.5))
    
    # Length-based (longer = more important)
    total_len = len(user_msg) + len(assistant_msg)
    if total_len > 500:
        importance = min(1.0, importance + 0.2)
    elif total_len > 200:
        importance = min(1.0, importance + 0.1)
    
    # Question marks indicate information seeking
    if "?" in user_msg:
        importance = min(1.0, importance + 0.1)
    
    # Explicit memory commands
    if any(kw in user_msg.lower() for kw in ("remember", "note", "save", "important")):
        importance = min(1.0, importance + 0.3)
    
    return importance


def get_recent_conversations(count: int = 6) -> list[dict]:
    """Return the most recent N conversations from in-memory buffer."""
    buf = _ensure_buffer()
    return list(buf)[-count:]


def get_important_conversations(min_importance: float = 0.7, limit: int = 20) -> list[dict]:
    """Get conversations above importance threshold."""
    buf = _ensure_buffer()
    return [c for c in buf if c.get("importance", 1.0) >= min_importance][-limit:]


def summarize_conversation_history(max_turns: int = 50) -> str:
    """Generate a summary of recent conversation history for context."""
    buf = _ensure_buffer()
    recent = list(buf)[-max_turns:]
    
    if not recent:
        return ""
    
    # Group by topic/tool
    topics = {}
    for turn in recent:
        tool = turn.get("tool", "chat")
        if tool not in topics:
            topics[tool] = []
        topics[tool].append(turn)
    
    summary_parts = []
    for tool, turns in topics.items():
        if tool == "chat":
            continue
        summary_parts.append(f"{tool}: {len(turns)} interactions")
    
    return "Recent activity: " + "; ".join(summary_parts) if summary_parts else ""


def invalidate_conversations_cache() -> None:
    """Invalidates the in-memory conversations cache."""
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
                    "importance": meta.get("importance", 1.0),
                })
            items.sort(key=lambda x: x.get("timestamp", ""))
            with _conv_cache_lock:
                _all_conversations_cache = list(items)
            return items
    except Exception as e:
        logger.debug("[RAG v2] get_all_conversations note: %s", e)

    fallback = list(_ensure_buffer())
    with _conv_cache_lock:
        _all_conversations_cache = list(fallback)
    return fallback


def clear_conversations() -> None:
    """Clear all conversation memory."""
    global _conversation_buffer, _all_conversations_cache
    _conversation_buffer = collections.deque(maxlen=_BUFFER_MAX)
    with _conv_cache_lock:
        _all_conversations_cache = []

    try:
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
        logger.error("[RAG v2] Error clearing conversations: %s", e)

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
#  User Facts with Importance
# ═══════════════════════════════════════════════════════════════

def add_user_fact(fact: str, category: str = "general", importance: float = 1.0) -> None:
    """Store a user fact in RAG memory with importance scoring."""
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
                    "importance": importance,
                }],
            )
    except Exception as e:
        logger.error("[RAG v2] Error adding user fact: %s", e)


def get_all_user_facts(min_importance: float = 0.0) -> list[str]:
    """Return all stored user facts, optionally filtered by importance."""
    try:
        col = _col(USER_FACTS)
        if col and col.count() > 0:
            results = col.get(limit=200, include=["documents", "metadatas"])
            if results and results.get("documents"):
                facts = []
                for doc, meta in zip(results["documents"], results["metadatas"]):
                    if meta.get("importance", 1.0) >= min_importance:
                        facts.append(doc)
                return facts
    except Exception:
        pass
    return []


def clear_user_facts() -> None:
    """Clear all user facts from the USER_FACTS collection."""
    try:
        col = _col(USER_FACTS)
        if col and col.count() > 0:
            # Get all IDs and delete them
            results = col.get(include=["ids"])
            if results and results.get("ids"):
                col.delete(ids=results["ids"])
                logger.info("[RAG v2] Cleared all user facts")
    except Exception as e:
        logger.error("[RAG v2] Error clearing user facts: %s", e)


# ═══════════════════════════════════════════════════════════════
#  Query Expansion & Rewriting
# ═══════════════════════════════════════════════════════════════

def expand_query(query: str, conversation_history: list | None = None) -> list[str]:
    """
    Expand query with synonyms, related terms, and context from history.
    Returns list of query variations for better recall.
    """
    if not query or not query.strip():
        return [query]
    
    queries = [query.strip()]
    q_lower = query.lower()
    
    # Add synonyms for common terms
    synonyms = {
        "find": ["search", "locate", "look for"],
        "show": ["display", "view", "see"],
        "open": ["launch", "start", "run"],
        "close": ["quit", "exit", "terminate"],
        "play": ["listen", "stream", "queue"],
        "weather": ["forecast", "temperature", "conditions"],
        "email": ["mail", "message", "inbox"],
        "calendar": ["schedule", "appointment", "meeting"],
        "document": ["file", "doc", "paper"],
        "remind": ["reminder", "alert", "notify"],
        "timer": ["countdown", "alarm", "stopwatch"],
    }
    
    for term, syns in synonyms.items():
        if term in q_lower:
            for syn in syns:
                queries.append(query.replace(term, syn))
    
    # Add context from conversation history
    if conversation_history:
        recent_topics = []
        for turn in conversation_history[-3:]:
            user_msg = turn.get("user", "").lower()
            # Extract key nouns/topics
            words = re.findall(r'\b[a-z]{4,}\b', user_msg)
            recent_topics.extend(words)
        
        # Add top topics to query
        from collections import Counter
        topic_counts = Counter(recent_topics)
        for topic, count in topic_counts.most_common(3):
            if topic not in q_lower:
                queries.append(f"{query} {topic}")
    
    # Deduplicate while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique_queries.append(q)
    
    return unique_queries[:5]  # Limit to 5 variations


# ═══════════════════════════════════════════════════════════════
#  Hybrid Search (Semantic + BM25 + Reranking)
# ═══════════════════════════════════════════════════════════════

def clear_search_cache() -> None:
    """Clear memory search cache."""
    _SEARCH_CACHE.clear()


def _semantic_search(query: str, target_collections: list[str], top_k: int) -> list[SearchResult]:
    """Perform semantic search using ChromaDB."""
    all_results = []
    
    for col_name in target_collections:
        try:
            col = _col(col_name)
            if not col or col.count() == 0:
                continue
            results = col.query(
                query_texts=[query],
                n_results=min(top_k * 2, col.count()),  # Get more for reranking
                include=["documents", "metadatas", "distances"],
            )
            if results and results.get("documents") and results["documents"][0]:
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                ):
                    all_results.append(SearchResult(
                        text=doc,
                        source=col_name,
                        score=round(1.0 - dist, 4),
                        metadata=meta,
                    ))
        except Exception as e:
            logger.debug("[RAG v2] Semantic search error in %s: %s", col_name, e)
    
    return all_results


def _bm25_search(query: str, top_k: int) -> list[SearchResult]:
    """Perform BM25 keyword search."""
    if _bm25_index is None or not _bm25_doc_map:
        return []
    
    try:
        from rank_bm25 import BM25Okapi
        query_tokens = query.lower().split()
        scores = _bm25_index.get_scores(query_tokens)
        
        # Get top-k indices
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        
        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                doc_info = _bm25_doc_map.get(idx)
                if doc_info:
                    results.append(SearchResult(
                        text=doc_info['text'],
                        source=DOCUMENTS,
                        score=float(scores[idx]),
                        metadata=doc_info['metadata'],
                        keyword_score=float(scores[idx]),
                    ))
        return results
    except Exception as e:
        logger.debug("[RAG v2] BM25 search error: %s", e)
        return []


def _rerank_results(query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]:
    """Rerank results using cross-encoder."""
    if _reranker is None or not results:
        return results[:top_k]
    
    try:
        # Prepare pairs for cross-encoder
        pairs = [(query, r.text) for r in results]
        scores = _reranker.predict(pairs)
        
        # Update rerank scores
        for r, score in zip(results, scores):
            r.rerank_score = float(score)
        
        # Sort by rerank score
        results.sort(key=lambda x: x.rerank_score, reverse=True)
        return results[:top_k]
    except Exception as e:
        logger.debug("[RAG v2] Reranking error: %s", e)
        return results[:top_k]


def _hybrid_search(query: str, target_collections: list[str], top_k: int = 5) -> list[SearchResult]:
    """
    Perform hybrid search combining semantic, keyword, and reranking.
    """
    # Expand query for better recall
    expanded_queries = expand_query(query)
    
    all_results = []
    
    # Semantic search for each expanded query
    for eq in expanded_queries:
        semantic_results = _semantic_search(eq, target_collections, top_k * 2)
        all_results.extend(semantic_results)
    
    # BM25 keyword search for documents
    if DOCUMENTS in target_collections:
        bm25_results = _bm25_search(query, top_k * 2)
        all_results.extend(bm25_results)
    
    # Deduplicate by text content
    seen_texts = set()
    unique_results = []
    for r in all_results:
        text_key = r.text[:200]  # Use first 200 chars as key
        if text_key not in seen_texts:
            seen_texts.add(text_key)
            unique_results.append(r)
    
    # Rerank with cross-encoder
    reranked = _rerank_results(query, unique_results, top_k * 2)
    
    # Final scoring: combine semantic, keyword, and rerank scores
    for r in reranked:
        # Weighted combination
        r.score = (
            0.4 * r.score +           # Semantic similarity
            0.2 * min(r.keyword_score / 10.0, 1.0) +  # Normalized BM25
            0.4 * r.rerank_score      # Cross-encoder relevance
        ) * r.importance
    
    reranked.sort(key=lambda x: x.score, reverse=True)
    return reranked[:top_k]


def search(query: str, target_collections: list[str] | None = None, top_k: int = 5) -> list[dict]:
    """
    Enhanced hybrid search with caching, query expansion, and reranking.
    Includes fast path for simple queries.
    """
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

    # Fast path for simple queries (short, no expansion needed)
    is_simple = len(query.split()) <= 3 and not any(c in query for c in '"\'')
    if is_simple and top_k <= 3:
        # Use only semantic search for simple queries (faster)
        results = _semantic_search(query, target_collections, top_k)
    else:
        # Full hybrid search for complex queries
        results = _hybrid_search(query, target_collections, top_k)
    
    # Convert to dict format for compatibility
    dict_results = []
    for r in results:
        dict_results.append({
            "text": r.text,
            "source": r.source,
            "score": r.score,
            "metadata": r.metadata,
            "rerank_score": r.rerank_score,
            "keyword_score": r.keyword_score,
        })

    # Cache results
    if len(_SEARCH_CACHE) > 512:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    _SEARCH_CACHE[cache_key] = (now, dict_results)
    
    return dict_results


# Convenience search functions
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
#  Enhanced RAG Context Builder
# ═══════════════════════════════════════════════════════════════

def build_rag_context(query: str, top_k: int = 5, conversation_history: list | None = None) -> str:
    """
    Build enhanced RAG context for LLM prompt injection.
    Uses hybrid search with query expansion and reranking.
    """
    if not query or not query.strip():
        return ""

    # Search with expanded queries and reranking
    results = search(query, target_collections=[DOCUMENTS, USER_FACTS], top_k=top_k)
    
    if not results:
        return ""

    # Filter by relevance threshold
    relevant = [r for r in results if r.get("score", 0) >= 0.3]
    if not relevant:
        return ""

    parts: list[str] = []
    top_doc = None
    seen_files: set[str] = set()

    for r in relevant:
        if len(seen_files) >= top_k:
            break
        src = r.get("source", "")
        if src == DOCUMENTS:
            meta = r.get("metadata", {})
            fname = meta.get("filename", "document")
            fpath = meta.get("filepath", "")
            if not top_doc and fpath and os.path.exists(fpath):
                top_doc = {"path": fpath, "name": fname}
            if fpath and fpath not in seen_files:
                seen_files.add(fpath)
                if os.path.exists(fpath):
                    # Use build_file_context which handles both small and large documents
                    file_context = build_file_context(fpath, query)
                    if file_context:
                        parts.append(file_context)
                        continue
                text = r.get("text", "")[:1500]  # Increased preview
                parts.append(f"[From document '{fname}']:\n{text}")
        elif src == USER_FACTS:
            text = r.get("text", "")[:1000]
            parts.append(f"[Known fact]: {text}")

    if top_doc:
        try:
            update_active_state("active_file", top_doc)
        except Exception:
            pass

    return "\n\n".join(parts)


def build_file_context(filepath: str, question: str) -> str:
    """Retrieve relevant chunks or full text from a specific file for Q&A."""
    if not os.path.exists(filepath):
        return ""
    text = extract_text(filepath)
    if not text:
        return ""

    # For short documents, provide full content
    if len(text) <= 8000:
        return f"[Full content of '{os.path.basename(filepath)}']:\n{text}"

    # For larger documents, query relevant chunks
    try:
        col = _col(DOCUMENTS)
        if col and col.count() > 0:
            seen_paths = set()
            for p in (filepath, os.path.normpath(filepath), filepath.replace("\\", "/"), filepath.replace("/", "\\")):
                if p in seen_paths:
                    continue
                seen_paths.add(p)
                results = col.query(
                    query_texts=[question],
                    n_results=10,
                    where={"filepath": p},
                    include=["documents", "distances"],
                )
                if results and results.get("documents") and results["documents"][0]:
                    return f"[Relevant sections from '{os.path.basename(filepath)}']:\n" + "\n\n".join(results["documents"][0])
    except Exception as e:
        logger.debug("[RAG v2] build_file_context error: %s", e)

    return f"[Content from '{os.path.basename(filepath)}']:\n{text[:8000]}"


def get_file_summary_context(filepath: str) -> str:
    """Extract text from a file for LLM summarization."""
    text = extract_text(filepath)
    if not text:
        return ""
    if len(text) <= 8000:
        return f"[Full content of '{os.path.basename(filepath)}']:\n{text}"
    return f"[Content from '{os.path.basename(filepath)}']:\n{text[:8000]}"


# ═══════════════════════════════════════════════════════════════
#  Document Indexing with Enhanced Chunking
# ═══════════════════════════════════════════════════════════════

def index_document(filepath: str) -> bool:
    """Index a single document into the vector store with semantic chunking."""
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

    # Use semantic chunking
    chunks = semantic_chunk(text)
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
        
        # Trigger BM25 index rebuild
        _bm25_update_queue.put(True)
        clear_search_cache()
        
        logger.info("[RAG v2] Indexed '%s' (%d semantic chunks)", filename, len(chunks))
        return True

    except Exception as e:
        logger.error("[RAG v2] Indexing error for '%s': %s", filepath, e)
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
        logger.error("[RAG v2] Error indexing email: %s", e)


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
        logger.error("[RAG v2] Error indexing calendar event: %s", e)


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
            add_user_fact(f"User's name is {name}", category="identity", importance=0.9)
            updated = True

    # Favorite Artist
    if m := re.search(r"\b(?:my (?:favorite|favourite) artist is|i love listening to)\s+([A-Za-z0-9\s]{2,40})", text, re.I):
        artist = m.group(1).strip().title()
        favs = preferences.setdefault("favorite_artists", [])
        if artist not in favs:
            favs.append(artist)
            preferences["favorite_artists"] = favs[-10:]
            add_user_fact(f"Favorite artist: {artist}", category="preference", importance=0.7)
            updated = True

    # Favorite City
    if m := re.search(r"\b(?:i live in|my city is)\s+([A-Za-z\s]{2,40})", text, re.I):
        city = m.group(1).strip().title()
        preferences["favorite_city"] = city
        add_user_fact(f"User lives in {city}", category="preference", importance=0.7)
        updated = True

    # Custom facts via "remember" field
    if remember and len(remember.strip()) > 5:
        add_user_fact(remember.strip(), category="custom", importance=0.8)
        updated = True

    if updated:
        save_profile(profile)


def get_user_profile_prompt(query: str = "") -> str:
    """Format user profile for LLM prompt injection with semantic fact retrieval."""
    profile = load_profile()
    parts: list[str] = []
    user_name = profile.get("identity", {}).get("name")
    if user_name:
        parts.append(f"The user's name is {user_name}. You know their name. Do NOT say 'Hello {user_name}!' repeatedly on every turn in an ongoing conversation; speak naturally as a companion.")
    if artists := profile.get("preferences", {}).get("favorite_artists"):
        parts.append(f"User's favorite artists: {', '.join(artists[:3])}.")
    if city := profile.get("preferences", {}).get("favorite_city"):
        parts.append(f"User lives in: {city}.")

    # Only inject specific remembered facts when semantically relevant
    if query and query.strip():
        q_clean = query.strip().lower()
        words = q_clean.split()
        is_memory_signal = any(kw in q_clean for kw in ("remember", "recall", "my ", "favorite", "about me", "note", "prefer", "know about me", "where do i", "who is", "what is my"))
        should_search_facts = (is_memory_signal or len(words) >= 4) and not any(
            q_clean.startswith(prefix) for prefix in ("open ", "launch ", "close ", "play ", "pause", "mute", "unmute", "volume ", "set timer", "scroll ", "click ")
        )
        if should_search_facts:
            try:
                matched_facts = search(query, target_collections=[USER_FACTS], top_k=3)
                for mf in matched_facts:
                    doc = (mf.get("text") or mf.get("document") or "").strip()
                    score = mf.get("score", 0)
                    if doc and score > 0.4 and not (user_name and doc.lower().startswith("user's name is")) and not doc.startswith("Uploaded image"):
                        parts.append(f"Relevant remembered fact: {doc}")
            except Exception as e:
                logger.debug("[RAG v2 Profile Facts]: %s", e)

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
    """Returns a memory dict shaped for UI and agent consumers."""
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
    """Initialize RAG engine asynchronously in the background."""
    global _initialized
    if _initialized:
        return

    def _worker():
        global _initialized
        try:
            _init_chroma()
            _initialized = True
            logger.info("[RAG v2] Engine ready in background. %s", get_index_stats())
        except Exception as e:
            logger.warning("[RAG v2] Background init note: %s", e)

    if background:
        t = threading.Thread(target=_worker, daemon=True, name="RAG-Init-Thread")
        t.start()
    else:
        _worker()


# ═══════════════════════════════════════════════════════════════
#  Async Search Interface
# ═══════════════════════════════════════════════════════════════

def search_async(query: str, target_collections: list[str] | None = None, top_k: int = 5, callback=None):
    """Perform search asynchronously with callback."""
    def _search_task():
        results = search(query, target_collections, top_k)
        if callback:
            callback(results)
    
    _executor.submit(_search_task)


# Backward compatibility aliases
smart_chunk = semantic_chunk