"""
RAG Background Indexer for Amigo Voice Assistant.
Crawls user directories, deduplicates symlinked/OneDrive paths via canonical resolution,
indexes new/modified files into ChromaDB, and periodically re-indexes on a background thread.
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger("amigo.rag_indexer")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))
_HOME = os.path.expanduser("~")

FILE_HASHES_PATH = os.path.join(_PROJECT_ROOT, "rag_data", "file_hashes.json")

DEFAULT_SCAN_DIRS = [
    os.path.join(_HOME, "Documents"),
    os.path.join(_HOME, "OneDrive", "Documents"),
    os.path.join(_HOME, "OneDrive - Personal", "Documents"),
    os.path.join(_HOME, "Desktop"),
    os.path.join(_HOME, "OneDrive", "Desktop"),
    os.path.join(_HOME, "OneDrive - Personal", "Desktop"),
    os.path.join(_HOME, "Downloads"),
]

SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env", "envs", "__pycache__",
    ".idea", ".vscode", ".cache", ".npm", ".nuget", "appdata", ".checkpoints",
    "rag_data", "dist", "build", ".next", ".turbo", "vendor", "target",
    "bin", "obj", "packages", "site-packages", "temp", "tmp",
    "models", ".models", "weights",
}


class RAGIndexer:
    """Background file indexer that crawls directories and indexes into ChromaDB."""

    def __init__(self, rag_engine=None):
        self._rag = rag_engine
        self._file_hashes: dict[str, str] = {}
        self._is_indexing = False
        self._index_lock = threading.Lock()
        self._daemon_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stats = {
            "files_indexed": 0,
            "files_skipped": 0,
            "files_total": 0,
            "files_processed": 0,
            "files_left": 0,
            "progress_percent": 0.0,
            "current_file": "",
            "status_message": "Idle",
            "last_run": None,
            "last_duration_seconds": 0,
            "is_indexing": False,
        }
        self._load_hashes()

    def _load_hashes(self) -> None:
        if os.path.exists(FILE_HASHES_PATH):
            try:
                with open(FILE_HASHES_PATH, "r", encoding="utf-8") as f:
                    raw_hashes = json.load(f)
                # Normalize loaded keys to canonical realpaths
                self._file_hashes = {
                    os.path.realpath(os.path.abspath(k)): v for k, v in raw_hashes.items()
                }
            except Exception:
                self._file_hashes = {}

    def _save_hashes(self) -> None:
        try:
            os.makedirs(os.path.dirname(FILE_HASHES_PATH), exist_ok=True)
            with open(FILE_HASHES_PATH, "w", encoding="utf-8") as f:
                json.dump(self._file_hashes, f)
        except Exception as e:
            logger.debug("[Indexer] Hash save note: %s", e)

    def _file_hash(self, filepath: str) -> str:
        try:
            canonical = os.path.realpath(os.path.abspath(filepath))
            stat = os.stat(canonical)
            key = f"{canonical}::{stat.st_mtime}::{stat.st_size}"
            return hashlib.sha256(key.encode()).hexdigest()[:16]
        except OSError:
            return ""

    def _file_changed(self, filepath: str) -> bool:
        canonical = os.path.realpath(os.path.abspath(filepath))
        current_hash = self._file_hash(canonical)
        if not current_hash:
            return False
        return self._file_hashes.get(canonical) != current_hash

    def get_scan_directories(self) -> list[str]:
        raw_dirs = []
        if self._rag:
            try:
                profile = self._rag.load_profile()
                custom_dirs = profile.get("preferences", {}).get("rag_scan_dirs", [])
                if custom_dirs:
                    raw_dirs = [d for d in custom_dirs if os.path.isdir(d)]
            except Exception:
                pass

        if not raw_dirs:
            raw_dirs = [d for d in DEFAULT_SCAN_DIRS if os.path.isdir(d)]

        downloads = os.path.join(_HOME, "Downloads")
        if os.path.isdir(downloads):
            raw_dirs.append(downloads)

        # Deduplicate directories that resolve to the same physical path (e.g. OneDrive junctions)
        unique_dirs: list[str] = []
        seen_real: set[str] = set()
        for d in raw_dirs:
            real_d = os.path.realpath(os.path.abspath(d)).lower()
            if real_d not in seen_real:
                seen_real.add(real_d)
                unique_dirs.append(os.path.realpath(os.path.abspath(d)))

        return unique_dirs

    def _discover_files(self, directories: list[str]) -> list[str]:
        files: list[str] = []
        seen_files: set[str] = set()

        supported_exts = set()
        max_size = 50 * 1024 * 1024
        if self._rag and hasattr(self._rag, "SUPPORTED_EXTENSIONS"):
            supported_exts = self._rag.SUPPORTED_EXTENSIONS
            max_size = getattr(self._rag, "MAX_FILE_SIZE", max_size)

        for scan_dir in directories:
            if not os.path.isdir(scan_dir):
                continue
            try:
                for root, dirs, filenames in os.walk(scan_dir, topdown=True):
                    dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]

                    for fname in filenames:
                        if fname.startswith("~$") or fname.startswith(".~"):
                            continue
                        ext = os.path.splitext(fname)[1].lower()
                        if supported_exts and ext not in supported_exts:
                            continue
                        filepath = os.path.realpath(os.path.abspath(os.path.join(root, fname)))
                        if filepath.lower() in seen_files:
                            continue
                        try:
                            fsize = os.path.getsize(filepath)
                            if 10 < fsize < max_size:
                                seen_files.add(filepath.lower())
                                files.append(filepath)
                        except OSError:
                            continue
            except PermissionError:
                continue

        return files

    def full_index(self, force: bool = False, progress_cb: Callable | None = None) -> dict:
        with self._index_lock:
            if self._is_indexing:
                return {"status": "already_running"}
            self._is_indexing = True

        self._stats.update({
            "is_indexing": True,
            "status_message": "Scanning directories...",
            "files_total": 0,
            "files_processed": 0,
            "files_indexed": 0,
            "files_skipped": 0,
            "files_left": 0,
            "progress_percent": 0.0,
            "current_file": "",
        })
        start_time = time.time()
        indexed = 0
        skipped = 0
        errors = 0

        try:
            if self._rag and hasattr(self._rag, "_col") and hasattr(self._rag, "DOCUMENTS"):
                try:
                    col = self._rag._col(self._rag.DOCUMENTS)
                    if col is not None and col.count() == 0 and self._file_hashes:
                        logger.warning("[Indexer] Chroma is empty but hash cache isn't; forcing re-index")
                        self._file_hashes.clear()
                        force = True
                except Exception:
                    pass

            directories = self.get_scan_directories()
            files = self._discover_files(directories)
            total = len(files)
            self._stats["files_total"] = total
            self._stats["files_left"] = total
            logger.info("[Indexer] Full index (force=%s): %d files found in %s", force, total, directories)

            deleted = [fp for fp in list(self._file_hashes.keys()) if not os.path.exists(fp)]
            for df in deleted:
                self._file_hashes.pop(df, None)
                if self._rag and hasattr(self._rag, "_col") and hasattr(self._rag, "DOCUMENTS"):
                    try:
                        col = self._rag._col(self._rag.DOCUMENTS)
                        if col:
                            col.delete(where={"filepath": os.path.realpath(os.path.abspath(df))})
                    except Exception:
                        pass

            if progress_cb:
                try:
                    progress_cb(0, total, 0, 0, "Discovered files...")
                except Exception:
                    pass

            for i, filepath in enumerate(files):
                if self._stop_event.is_set():
                    break

                fname = os.path.basename(filepath)
                pct = round(((i + 1) / max(total, 1)) * 100, 1)
                self._stats.update({
                    "files_processed": i + 1,
                    "files_left": max(0, total - (i + 1)),
                    "progress_percent": pct,
                    "current_file": fname,
                    "status_message": f"Processing ({i + 1}/{total}): {fname}",
                })

                if not force and not self._file_changed(filepath):
                    skipped += 1
                    self._stats["files_skipped"] = skipped
                    if progress_cb:
                        try:
                            progress_cb(i + 1, total, indexed, skipped, fname)
                        except Exception:
                            pass
                    continue

                try:
                    if self._rag and self._rag.index_document(filepath):
                        indexed += 1
                        self._file_hashes[filepath] = self._file_hash(filepath)
                        self._stats["files_indexed"] = indexed
                        self._save_hashes()
                    else:
                        logger.warning("[Indexer] No text indexed for %s (scanned/empty/unreadable?)", filepath)
                        self._file_hashes[filepath] = self._file_hash(filepath)
                        skipped += 1
                        self._stats["files_skipped"] = skipped
                        self._save_hashes()
                except Exception as e:
                    errors += 1
                    logger.debug("[Indexer] Error indexing %s: %s", filepath, e)

                if progress_cb:
                    try:
                        progress_cb(i + 1, total, indexed, skipped, fname)
                    except Exception:
                        pass

                time.sleep(0.05)

            self._save_hashes()
            if indexed > 0 and self._rag and hasattr(self._rag, "_schedule_bm25_rebuild"):
                try:
                    self._rag._schedule_bm25_rebuild()
                except Exception:
                    pass

        finally:
            duration = round(time.time() - start_time, 1)
            self._is_indexing = False
            total_scanned = indexed + skipped
            self._stats.update({
                "is_indexing": False,
                "files_processed": total_scanned,
                "files_indexed": indexed,
                "files_skipped": skipped,
                "files_left": 0,
                "progress_percent": 100.0 if total_scanned > 0 else 0.0,
                "current_file": "",
                "status_message": f"Complete: {indexed} indexed, {skipped} skipped ({duration}s)",
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "last_duration_seconds": duration,
            })
            logger.info(
                "[Indexer] Complete: %d indexed, %d skipped, %d errors in %.1fs",
                indexed, skipped, errors, duration,
            )

        return self._stats.copy()

    def incremental_index(self) -> dict:
        return self.full_index(force=False)

    def index_single_file(self, filepath: str) -> bool:
        if not self._rag:
            return False
        canonical = os.path.realpath(os.path.abspath(filepath))
        ok = self._rag.index_document(canonical)
        if ok:
            self._file_hashes[canonical] = self._file_hash(canonical)
            self._save_hashes()
            if hasattr(self._rag, "_schedule_bm25_rebuild"):
                try:
                    self._rag._schedule_bm25_rebuild()
                except Exception:
                    pass
        return ok

    def start_background_daemon(self, interval_minutes: int = 30, initial_delay: int = 120) -> None:
        if self._daemon_thread and self._daemon_thread.is_alive():
            return

        self._stop_event.clear()

        def _daemon():
            logger.info("[Indexer] Background daemon started (interval=%dm).", interval_minutes)
            time.sleep(initial_delay)

            while not self._stop_event.is_set():
                try:
                    self.full_index()
                except Exception as e:
                    logger.error("[Indexer] Daemon error: %s", e)

                self._stop_event.wait(timeout=interval_minutes * 60)

            logger.info("[Indexer] Background daemon stopped.")

        self._daemon_thread = threading.Thread(target=_daemon, daemon=True, name="rag-indexer")
        self._daemon_thread.start()

    def stop_daemon(self) -> None:
        self._stop_event.set()
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5)

    def get_status(self) -> dict:
        return {**self._stats, "is_indexing": self._is_indexing}


_indexer_instance: RAGIndexer | None = None


def get_indexer(rag_engine=None) -> RAGIndexer:
    global _indexer_instance
    if _indexer_instance is None:
        _indexer_instance = RAGIndexer(rag_engine)
    elif rag_engine is not None and _indexer_instance._rag is None:
        _indexer_instance._rag = rag_engine
    return _indexer_instance


def start_background_indexer(rag_engine, interval_minutes: int = 30) -> RAGIndexer:
    indexer = get_indexer(rag_engine)
    indexer.start_background_daemon(interval_minutes=interval_minutes)
    return indexer
