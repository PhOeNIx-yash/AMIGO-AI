"""
RAG Background Indexer for Amigo Voice Assistant.
Crawls user directories, indexes new/modified files into ChromaDB,
and periodically re-indexes on a background thread.
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
_HOME = os.path.expanduser("~")

FILE_HASHES_PATH = os.path.join(_BASE_DIR, "rag_data", "file_hashes.json")

# Default directories to scan (supports both standard and OneDrive-synced folders)
DEFAULT_SCAN_DIRS = [
    os.path.join(_HOME, "Documents"),
    os.path.join(_HOME, "OneDrive", "Documents"),
    os.path.join(_HOME, "Desktop"),
    os.path.join(_HOME, "OneDrive", "Desktop"),
    os.path.join(_HOME, "Downloads"),
]

# Skip these directory names entirely
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env", "envs", "__pycache__",
    ".idea", ".vscode", ".cache", ".npm", ".nuget", "AppData", ".checkpoints",
    "rag_data", "dist", "build", ".next", ".turbo", "vendor", "target",
    "bin", "obj", "packages", "site-packages", "temp", "tmp",
}


class RAGIndexer:
    """Background file indexer that crawls directories and indexes into ChromaDB."""

    def __init__(self, rag_engine=None):
        self._rag = rag_engine
        self._file_hashes: dict[str, str] = {}
        self._is_indexing = False
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
        """Load file hash cache from disk."""
        if os.path.exists(FILE_HASHES_PATH):
            try:
                with open(FILE_HASHES_PATH, "r", encoding="utf-8") as f:
                    self._file_hashes = json.load(f)
            except Exception:
                self._file_hashes = {}

    def _save_hashes(self) -> None:
        """Persist file hash cache to disk."""
        try:
            os.makedirs(os.path.dirname(FILE_HASHES_PATH), exist_ok=True)
            with open(FILE_HASHES_PATH, "w", encoding="utf-8") as f:
                json.dump(self._file_hashes, f)
        except Exception as e:
            logger.debug("[Indexer] Hash save note: %s", e)

    def _file_hash(self, filepath: str) -> str:
        """SHA256 hash based on filepath + modification time + size (fast, no content read)."""
        try:
            stat = os.stat(filepath)
            key = f"{filepath}::{stat.st_mtime}::{stat.st_size}"
            return hashlib.sha256(key.encode()).hexdigest()[:16]
        except OSError:
            return ""

    def _file_changed(self, filepath: str) -> bool:
        """Check if a file has been modified since last indexing."""
        current_hash = self._file_hash(filepath)
        if not current_hash:
            return False
        return self._file_hashes.get(filepath) != current_hash

    def get_scan_directories(self) -> list[str]:
        """Return list of directories to scan."""
        if self._rag:
            profile = self._rag.load_profile()
            custom_dirs = profile.get("preferences", {}).get("rag_scan_dirs", [])
            if custom_dirs:
                return [d for d in custom_dirs if os.path.isdir(d)]

        return [d for d in DEFAULT_SCAN_DIRS if os.path.isdir(d)]

    def _discover_files(self, directories: list[str]) -> list[str]:
        """Walk directories and return list of indexable file paths."""
        files: list[str] = []

        for scan_dir in directories:
            if not os.path.isdir(scan_dir):
                continue
            try:
                for root, dirs, filenames in os.walk(scan_dir, topdown=True):
                    # Prune skipped directories
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

                    for fname in filenames:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext not in self._rag.SUPPORTED_EXTENSIONS:
                            continue
                        filepath = os.path.join(root, fname)
                        try:
                            fsize = os.path.getsize(filepath)
                            if 10 < fsize < self._rag.MAX_FILE_SIZE:
                                files.append(filepath)
                        except OSError:
                            continue
            except PermissionError:
                continue

        return files

    def full_index(self, progress_cb: Callable | None = None) -> dict:
        """Full crawl and index of all scan directories."""
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
            directories = self.get_scan_directories()
            files = self._discover_files(directories)
            total = len(files)
            self._stats["files_total"] = total
            self._stats["files_left"] = total
            logger.info("[Indexer] Full index: %d files found in %s", total, directories)

            for i, filepath in enumerate(files):
                if self._stop_event.is_set():
                    break

                fname = os.path.basename(filepath)
                pct = round((i / max(total, 1)) * 100, 1)
                self._stats.update({
                    "files_processed": i,
                    "files_left": max(0, total - i),
                    "progress_percent": pct,
                    "current_file": fname,
                    "status_message": f"Processing ({i + 1}/{total}): {fname}",
                })

                if not self._file_changed(filepath):
                    skipped += 1
                    self._stats["files_skipped"] = skipped
                    continue

                try:
                    if self._rag.index_document(filepath):
                        indexed += 1
                        self._file_hashes[filepath] = self._file_hash(filepath)
                        self._stats["files_indexed"] = indexed
                    else:
                        skipped += 1
                        self._stats["files_skipped"] = skipped
                except Exception as e:
                    errors += 1
                    logger.debug("[Indexer] Error indexing %s: %s", filepath, e)

                if progress_cb and (i + 1) % 5 == 0:
                    progress_cb(i + 1, total, indexed)

            self._save_hashes()

            # Update profile stats
            if self._rag:
                profile = self._rag.load_profile()
                profile.setdefault("stats", {})["rag_indexed_files"] = indexed + skipped
                self._rag.save_profile(profile)

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
                "status_message": f"Complete: {indexed} indexed, {skipped} up-to-date ({duration}s)",
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "last_duration_seconds": duration,
            })
            logger.info(
                "[Indexer] Complete: %d indexed, %d skipped, %d errors in %.1fs",
                indexed, skipped, errors, duration,
            )

        return self._stats.copy()

    def incremental_index(self) -> dict:
        """Re-index only new or modified files since last scan."""
        return self.full_index()

    def index_single_file(self, filepath: str) -> bool:
        """Index a single file on demand."""
        if not self._rag:
            return False
        ok = self._rag.index_document(filepath)
        if ok:
            self._file_hashes[filepath] = self._file_hash(filepath)
            self._save_hashes()
        return ok

    def start_background_daemon(self, interval_minutes: int = 30, initial_delay: int = 10) -> None:
        """Start background indexing daemon thread."""
        if self._daemon_thread and self._daemon_thread.is_alive():
            return

        self._stop_event.clear()

        def _daemon():
            logger.info("[Indexer] Background daemon started (interval=%dm).", interval_minutes)
            # Wait before initial scan so server boots fast
            time.sleep(initial_delay)

            while not self._stop_event.is_set():
                try:
                    self.full_index()
                except Exception as e:
                    logger.error("[Indexer] Daemon error: %s", e)

                # Wait for next interval
                self._stop_event.wait(timeout=interval_minutes * 60)

            logger.info("[Indexer] Background daemon stopped.")

        self._daemon_thread = threading.Thread(target=_daemon, daemon=True, name="rag-indexer")
        self._daemon_thread.start()

    def stop_daemon(self) -> None:
        """Stop the background indexing daemon."""
        self._stop_event.set()
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5)

    def get_status(self) -> dict:
        """Return current indexing status."""
        return {**self._stats, "is_indexing": self._is_indexing}


# ── Module-level singleton ─────────────────────────────────────
_indexer_instance: RAGIndexer | None = None


def get_indexer(rag_engine=None) -> RAGIndexer:
    """Get or create the global RAGIndexer singleton."""
    global _indexer_instance
    if _indexer_instance is None:
        _indexer_instance = RAGIndexer(rag_engine)
    return _indexer_instance


def start_background_indexer(rag_engine, interval_minutes: int = 30) -> RAGIndexer:
    """Convenience: create indexer and start its background daemon."""
    indexer = get_indexer(rag_engine)
    indexer.start_background_daemon(interval_minutes=interval_minutes)
    return indexer
