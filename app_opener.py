"""
app_opener.py — Unified App Launcher + File Finder for Amigo Voice Assistant.

Single module handles:
  - Installing/querying a shared system index (apps + files) built once per session
  - Opening any installed Windows app by spoken name
  - Finding any file system-wide
  - Opening user folders in Explorer

Zero hardcoded paths, drive letters, or usernames.
Works on any Windows machine for any user.

Discovery sources (all dynamic):
  Apps  → Get-StartApps (UWP+Win32), HKLM App Paths, Start Menu .lnk,
           Uninstall registry, .exe filesystem scan across all drives
  Files → Windows Search Indexer (ADODB) first, os.walk fallback on all drives

Public API
----------
ensure_built()                    → start background index build at startup
open_windows_app(name)            → launch any installed app by spoken name
find_files(query, max_results)    → search files system-wide
open_file_or_location(path)       → open a file with its default app
open_folder(name)                 → open a user folder in Explorer
refresh_app_index()               → force full index rebuild
"""

import glob
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
import winreg
from typing import Dict, List, Optional, Tuple


# ============================================================================
# Dynamic system helpers — zero hardcoded paths
# ============================================================================

_USER_HOME = os.path.expanduser("~")


def _get_all_drives() -> List[str]:
    """
    Discover all mounted drives dynamically via ctypes.GetLogicalDriveStringsW.
    Falls back to scanning A–Z if ctypes is unavailable.
    Works for any drive configuration without hardcoding any letter.
    """
    drives: List[str] = []
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.kernel32.GetLogicalDriveStringsW(len(buf), buf)
        drives = [d for d in buf.value.split("\x00") if d and os.path.isdir(d)]
    except Exception:
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            p = f"{letter}:\\"
            if os.path.isdir(p):
                drives.append(p)
    return drives


def _expandenv(var: str, default: str = "") -> str:
    val = os.environ.get(var, default)
    return os.path.expandvars(val) if val else default


def _start_menu_dirs() -> List[str]:
    """Both user and system Start Menu program directories, dynamically resolved."""
    dirs = []
    for var in ("%APPDATA%", "%ProgramData%"):
        p = os.path.join(os.path.expandvars(var), "Microsoft", "Windows", "Start Menu", "Programs")
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


def _exe_scan_dirs() -> List[Tuple[str, int]]:
    """
    (directory, max_depth) pairs for .exe scanning.
    Fully dynamic — uses env vars and discovered drives, no hardcoded letters.
    """
    dirs: List[Tuple[str, int]] = []
    # Windows system dirs
    sysroot = os.environ.get("SystemRoot") or os.path.join(
        os.environ.get("SystemDrive", "C:"), "Windows"
    )
    if os.path.isdir(sysroot):
        dirs.append((sysroot, 1))
        dirs.append((os.path.join(sysroot, "System32"), 1))
        dirs.append((os.path.join(sysroot, "SysWOW64"), 1))
    # Program Files on every mounted drive
    for drive in _get_all_drives():
        for pf in ("Program Files", "Program Files (x86)"):
            p = os.path.join(drive, pf)
            if os.path.isdir(p):
                dirs.append((p, 3))
    # User-local installs
    for var, depth in (("LOCALAPPDATA", 3), ("APPDATA", 2)):
        base = _expandenv(var)
        p = os.path.join(base, "Programs") if var == "LOCALAPPDATA" else base
        if os.path.isdir(p):
            dirs.append((p, depth))
    return dirs


# ============================================================================
# App index — built once per session in a background thread
# ============================================================================

_SKIP_EXE = frozenset({
    "uninstall", "uninst", "setup", "installer", "updater", "update",
    "crash", "report", "helper", "service", "agent", "squirrel",
    "crashpad", "elevate", "launcher_aux",
})

# Directories to skip entirely during file-system walks (noisy / system noise)
_SKIP_DIRS = frozenset({
    "windows", "system32", "syswow64", "winsxs", "drivers",
    "node_modules", ".git", ".svn", "__pycache__", ".venv", "venv",
    "site-packages", "dist-packages", "dist", "build",
    "temp", "tmp", "cache", "caches", "logs", "log",
    "appdata", "programdata",
})

_NOISE_WORDS = frozenset({
    "app", "application", "program", "software", "the",
    "open", "launch", "start", "me", "a", "an",
})

_app_dict:    Dict[str, str] = {}   # { normalised_name: exe_path or "uwp:AppID" }
_index_built: bool = False
_build_lock   = threading.Lock()
_build_thread: Optional[threading.Thread] = None


def _normalize(name: str) -> str:
    return " ".join(t for t in name.lower().split() if t not in _NOISE_WORDS).strip()


# --- App index source scanners ---

def _src_start_apps() -> Dict[str, str]:
    """PowerShell Get-StartApps → name: path (covers UWP/MSIX apps)."""
    out: Dict[str, str] = {}
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            timeout=15, stderr=subprocess.DEVNULL,
        ).decode(errors="replace").strip()
        apps = json.loads(raw)
        if isinstance(apps, dict):
            apps = [apps]
        for app in apps:
            name   = app.get("Name", "").lower().strip()
            app_id = app.get("AppID", "").strip()
            if not name or not app_id:
                continue
            if app_id.startswith(("http://", "https://")):
                continue
            if app_id.endswith((".txt", ".chm", ".html", ".htm", ".pdf")):
                continue
            if "!" in app_id or app_id.startswith(("Microsoft.", "windows.")):
                out[name] = f"uwp:{app_id}"
            else:
                full = os.path.expandvars(app_id)
                if full.startswith("{"):
                    rest = full[full.find("}") + 1:].lstrip("\\")
                    for drive in _get_all_drives():
                        for pf in ("Program Files", "Program Files (x86)"):
                            c = os.path.join(drive, pf, rest)
                            if os.path.isfile(c):
                                full = c; break
                    for ev in ("LOCALAPPDATA", "APPDATA"):
                        c = os.path.join(_expandenv(ev), rest)
                        if os.path.isfile(c):
                            full = c; break
                if os.path.isfile(full):
                    out[name] = full
    except Exception as e:
        print(f"[AppIndex] Get-StartApps note: {e}", flush=True)
    return out


def _src_app_paths_registry() -> Dict[str, str]:
    """HKLM App Paths — OS-maintained, zero hardcoding."""
    out: Dict[str, str] = {}
    try:
        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
        )
        i = 0
        while True:
            try:
                exe_filename = winreg.EnumKey(root, i); i += 1
            except OSError:
                break
            try:
                sub = winreg.OpenKey(root, exe_filename)
                try:
                    path_val, _ = winreg.QueryValueEx(sub, "")
                except FileNotFoundError:
                    sub.Close(); continue
                sub.Close()
                path_val = os.path.expandvars(path_val.strip().strip('"'))
                if path_val and os.path.isfile(path_val):
                    out[os.path.splitext(exe_filename)[0].lower().strip()] = path_val
            except Exception:
                pass
        root.Close()
    except Exception:
        pass
    return out


def _src_lnk_shortcuts() -> Dict[str, str]:
    """Start Menu .lnk → exe paths via WScript.Shell COM."""
    out: Dict[str, str] = {}
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        for base in _start_menu_dirs():
            for lnk in glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
                try:
                    sc = shell.CreateShortcut(lnk)
                    t = sc.TargetPath
                    if t and t.lower().endswith(".exe") and os.path.isfile(t):
                        out[os.path.splitext(os.path.basename(lnk))[0].lower().strip()] = t
                except Exception:
                    pass
    except Exception as e:
        print(f"[AppIndex] Shortcut scan note: {e}", flush=True)
    return out


def _src_uninstall_registry() -> Dict[str, str]:
    """Uninstall registry → product display names mapped to exe paths."""
    out: Dict[str, str] = {}
    hives = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in hives:
        try:
            root = winreg.OpenKey(hive, path)
        except FileNotFoundError:
            continue
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(root, i); i += 1
            except OSError:
                break
            try:
                sub = winreg.OpenKey(root, sub_name)
                try:
                    display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                    install_loc, _  = winreg.QueryValueEx(sub, "InstallLocation")
                except FileNotFoundError:
                    sub.Close(); continue
                if install_loc and os.path.isdir(install_loc):
                    nk = display_name.lower().strip()
                    matched = False
                    for fname in os.listdir(install_loc):
                        if fname.lower().endswith(".exe"):
                            stem = os.path.splitext(fname)[0].lower()
                            full = os.path.join(install_loc, fname)
                            if os.path.isfile(full) and (stem in nk or nk.startswith(stem)):
                                out[nk] = full; matched = True; break
                    if not matched:
                        for fname in os.listdir(install_loc):
                            if fname.lower().endswith(".exe"):
                                full = os.path.join(install_loc, fname)
                                if os.path.isfile(full):
                                    out[nk] = full; break
                sub.Close()
            except Exception:
                pass
        root.Close()
    return out


def _src_exe_scan(base_dir: str, max_depth: int, depth: int = 0) -> Dict[str, str]:
    """Walk directory tree collecting user-facing .exe files."""
    out: Dict[str, str] = {}
    if depth > max_depth or not os.path.isdir(base_dir):
        return out
    try:
        for entry in os.scandir(base_dir):
            if entry.is_dir(follow_symlinks=False):
                out.update(_src_exe_scan(entry.path, max_depth, depth + 1))
            elif entry.is_file() and entry.name.lower().endswith(".exe"):
                stem = os.path.splitext(entry.name)[0].lower().strip()
                if not any(kw in stem for kw in _SKIP_EXE) and stem not in out:
                    out[stem] = entry.path
    except PermissionError:
        pass
    return out


def _build_index() -> None:
    """Build the unified app dict from all sources and mark index ready."""
    global _app_dict, _index_built
    print("[AppIndex] Scanning system for installed apps ...", flush=True)
    t0 = time.time()
    idx: Dict[str, str] = {}
    idx.update(_src_start_apps())
    idx.update(_src_app_paths_registry())
    idx.update(_src_lnk_shortcuts())
    idx.update(_src_uninstall_registry())
    for base_dir, max_depth in _exe_scan_dirs():
        idx.update(_src_exe_scan(base_dir, max_depth))
    with _build_lock:
        _app_dict = idx
        _index_built = True
    print(f"[AppIndex] Ready — {len(_app_dict)} apps in {time.time()-t0:.1f}s", flush=True)


def ensure_built() -> None:
    """
    Start the background index build if not already started.
    Call at app startup — returns immediately, builds in background.
    """
    global _build_thread
    with _build_lock:
        if _index_built or (_build_thread and _build_thread.is_alive()):
            return
        _build_thread = threading.Thread(target=_build_index, daemon=True, name="AppIndex-Build")
        _build_thread.start()


def _wait_ready(timeout: float = 8.0) -> None:
    """Block until index is ready (max timeout seconds)."""
    ensure_built()
    deadline = time.time() + timeout
    while not _index_built and time.time() < deadline:
        time.sleep(0.05)
    if not _index_built:
        _build_index()  # synchronous last resort


def refresh_app_index() -> None:
    """Force a full index rebuild (call after installing new apps)."""
    global _index_built
    with _build_lock:
        _index_built = False
    ensure_built()


# ============================================================================
# App matching
# ============================================================================

def _find_best_app(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Match a spoken query against the app index.
    Returns (matched_name, path_or_uwp_id) or (None, None).
    """
    norm_q = _normalize(query)
    if not norm_q:
        return None, None

    candidates = list(_app_dict.keys())

    # 1. Exact
    if norm_q in _app_dict:
        return norm_q, _app_dict[norm_q]

    # 2. Candidate starts with query prefix
    if len(norm_q) >= 3:
        hits = [c for c in candidates if c.startswith(norm_q)]
        if hits:
            best = min(hits, key=len)
            return best, _app_dict[best]

    # 3. Query starts with a candidate alias
    if len(norm_q) >= 3:
        hits = [c for c in candidates if norm_q.startswith(c) and len(c) >= 3]
        if hits:
            best = max(hits, key=len)
            return best, _app_dict[best]

    # 4. All query tokens present as tokens in candidate
    q_toks = set(norm_q.split())
    if len(q_toks) >= 2:
        scored = [(len(set(c.split())), c) for c in candidates if q_toks <= set(c.split())]
        if scored:
            best = min(scored)[1]
            return best, _app_dict[best]

    # 5. Any long single query token is an exact candidate key
    if len(q_toks) >= 2:
        hits = [(len(t), t) for t in q_toks if t in _app_dict and len(t) >= 4]
        if hits:
            best = max(hits)[1]
            return best, _app_dict[best]

    return None, None


# ============================================================================
# File search — Windows Indexer first, os.walk fallback on all drives
# ============================================================================

# Universal spoken conversational filler words — stripped dynamically
_CLEAN_WORDS = frozenset({
    "can", "could", "would", "will", "please", "kindly",
    "find", "search", "locate", "look", "show", "get", "open", "where",
    "file", "files", "document", "documents", "name", "named", "called", "titled",
    "me", "my", "your", "our", "their", "his", "her", "its", "u", "you", "i",
    "a", "an", "the", "some", "any", "all", "recent", "latest",
    "for", "in", "on", "at", "to", "from", "with", "about", "of", "by",
    "is", "was", "are", "were", "has", "have", "had", "do", "does", "did",
    "belonging", "containing", "extension", "type"
})

_JUNK_EXTS = frozenset({
    ".dll", ".sys", ".msi", ".cat", ".inf", ".mui", ".pyc", ".bds", ".dat", ".bin", ".cur", ".ani", ".nls", ".tmp", ".etl", ".log"
})

_USER_EXTS = frozenset({
    ".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".png", ".jpg", ".jpeg", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".zip", ".mp3", ".mp4", ".wav", ".md"
})


def _clean_file_query(raw: str) -> str:
    """Extract meaningful keywords: 'can u find a file name yash resume' -> 'yash resume'."""
    raw_clean = re.sub(r"[^\w\s\.\-]", " ", raw.lower())
    tokens = [w.strip() for w in raw_clean.split() if w.strip()]
    meaningful = [t for t in tokens if t not in _CLEAN_WORDS and len(t) >= 2]
    return " ".join(meaningful if meaningful else [t for t in tokens if len(t) >= 2])


def _extract_keywords(clean_q: str) -> List[str]:
    """Extract keyword list for file matching."""
    tokens = clean_q.lower().split()
    meaningful = [t for t in tokens if t not in _CLEAN_WORDS and len(t) >= 2]
    return meaningful if meaningful else [t for t in tokens if len(t) >= 2]


def _score_file_candidate(path: str, name: str, tokens: List[str]) -> int:
    """Intelligent scoring: boosts real user documents/code and penalizes system junk dynamically."""
    p_lower = path.lower()
    n_lower = name.lower()
    _, ext = os.path.splitext(n_lower)

    if ext in _JUNK_EXTS:
        return -1000

    score = 0
    user_home_lower = _USER_HOME.lower()

    # User priority directories (dynamically determined from user profile)
    if p_lower.startswith(user_home_lower):
        score += 400
        try:
            rel = os.path.relpath(path, _USER_HOME)
            if not rel.startswith("."):
                score += 300
        except Exception:
            pass

    # User-friendly extensions (documents, media, code, data)
    if ext in _USER_EXTS:
        score += 200

    # System directories dynamically derived from OS environment
    sys_roots = [
        os.environ.get(k, "").lower()
        for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "LOCALAPPDATA", "APPDATA")
        if os.environ.get(k)
    ]
    if any(s and p_lower.startswith(s) for s in sys_roots):
        score -= 500

    if "node_modules" in p_lower or "__pycache__" in p_lower or ".git" in p_lower or "\\temp\\" in p_lower:
        score -= 600

    # Keyword match bonus in filename
    matched_count = sum(1 for t in tokens if t in n_lower)
    score += matched_count * 300

    # All tokens matched bonus
    if tokens and all(t in n_lower for t in tokens):
        score += 800

    return score


def _indexer_search(term: str, max_results: int = 50) -> List[Tuple[int, float, str, str]]:
    """Windows Search Indexer (ADODB) — instant system-wide file results with smart scoring."""
    results: List[Tuple[int, float, str, str]] = []  # (score, mtime, path, name)
    if not term:
        return results

    tokens = _extract_keywords(term)
    if not tokens:
        return results

    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            conn = win32com.client.Dispatch("ADODB.Connection")
            conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")

            def _run_sql(where: str) -> List[Tuple[str, str]]:
                sql = (
                    f"SELECT TOP {max_results} System.ItemPathDisplay, System.ItemNameDisplay "
                    f"FROM SystemIndex WHERE {where}"
                )
                rs, _ = conn.Execute(sql)
                rows: List[Tuple[str, str]] = []
                while not rs.EOF:
                    p = rs.Fields("System.ItemPathDisplay").Value
                    n = rs.Fields("System.ItemNameDisplay").Value
                    if p and os.path.isfile(p):
                        rows.append((p, n))
                    rs.MoveNext()
                return rows

            # Pass 1: All tokens AND'd
            where_and = " AND ".join([f"System.ItemNameDisplay LIKE '%{t}%'" for t in tokens])
            rows = _run_sql(where_and)

            # Pass 2: Fallback to OR if multi-token AND yielded no direct hits
            if not rows and len(tokens) > 1:
                where_or = " OR ".join([f"System.ItemNameDisplay LIKE '%{t}%'" for t in tokens])
                rows = _run_sql(where_or)

            seen: set = set()
            for path, name in rows:
                if path in seen:
                    continue
                seen.add(path)
                score = _score_file_candidate(path, name, tokens)
                if score <= 0:
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except Exception:
                    mtime = 0.0
                results.append((score, mtime, path, name))

            results.sort(key=lambda x: (x[0], x[1]), reverse=True)
            print(f"[FileSearch] ADODB '{tokens}' -> {len(results)} files", flush=True)
        finally:
            pythoncom.CoUninitialize()

    except Exception as e:
        print(f"[FileSearch] ADODB note: {e}", flush=True)

    return results


def _walk_search(keywords: List[str], seen: set, max_results: int = 50) -> List[Tuple[int, float, str, str]]:
    """
    os.walk fallback — all dynamically discovered drives.
    User home: depth 6.  Other drives: depth 3.  Zero hardcoded paths.
    """
    results: List[Tuple[int, float, str, str]] = []
    user_home_norm = os.path.normcase(_USER_HOME)

    # Build ordered roots: user home first, then other drives
    roots: List[str] = []
    try:
        for e in os.scandir(_USER_HOME):
            if e.is_dir() and not e.name.startswith("."):
                roots.append(e.path)
    except Exception:
        pass
    roots.insert(0, _USER_HOME)
    for drive in _get_all_drives():
        if not user_home_norm.startswith(os.path.normcase(drive)) and drive not in roots:
            roots.append(drive)

    for root in roots:
        if not os.path.exists(root):
            continue
        is_user  = os.path.normcase(root).startswith(user_home_norm)
        max_depth = 6 if is_user else 3

        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith((".", "$")) and d.lower() not in _SKIP_DIRS
                ]
                try:
                    rel = os.path.relpath(dirpath, root)
                    depth = 0 if rel == "." else len(rel.split(os.sep))
                except ValueError:
                    depth = 0
                if depth >= max_depth:
                    dirnames.clear()
                    continue

                for fname in filenames:
                    if fname.startswith(("~", ".", "$")):
                        continue
                    full = os.path.join(dirpath, fname)
                    if full in seen:
                        continue
                    score = _score_file_candidate(full, fname, keywords)
                    if score <= 0:
                        continue
                    try:
                        mtime = os.path.getmtime(full)
                        results.append((score, mtime, full, fname))
                        seen.add(full)
                    except Exception:
                        pass
                    if len(results) >= max_results:
                        break
        except Exception:
            pass

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return results


# ============================================================================
# User folder opener
# ============================================================================

def _user_subfolders() -> Dict[str, str]:
    """Dynamically maps folder names → paths for every top-level home subdirectory."""
    folders: Dict[str, str] = {"home": _USER_HOME, "user folder": _USER_HOME}
    try:
        for entry in os.scandir(_USER_HOME):
            if entry.is_dir() and not entry.name.startswith("."):
                n = entry.name.lower()
                folders[n] = entry.path
                if n.endswith("s"):
                    folders[n[:-1]] = entry.path
    except Exception:
        pass
    return folders


def open_folder(folder_name: str) -> Tuple[bool, str]:
    """Open any user folder in Windows Explorer by spoken name."""
    if not folder_name:
        folder_name = "downloads"

    clean = re.sub(
        r"^(my|the|open|show|folder|go to)\s*", "", folder_name.lower().strip(),
        flags=re.IGNORECASE
    ).replace("folder", "").strip()

    folders = _user_subfolders()
    target, matched = None, None
    for key, path in folders.items():
        if key in clean or clean in key:
            if os.path.exists(path):
                target, matched = path, key
                break

    if not target:
        target  = folders.get("downloads", _USER_HOME)
        matched = "downloads"

    try:
        print(f"[FileFinder] Opening folder '{matched}' -> '{target}'", flush=True)
        os.startfile(target)
        return True, f"Opening your {matched.capitalize()} folder."
    except Exception as e:
        print(f"[FileFinder] Error: {e}", flush=True)
        return False, f"Could not open folder {folder_name}."


# ============================================================================
# Public API
# ============================================================================

def open_windows_app(app_name: str) -> bool:
    """Open any installed Windows application by spoken name."""
    if not app_name:
        return False

    # Web URL / domain -> browser
    if re.search(
        r"^(https?://|www\.)|[a-zA-Z0-9-]+\.(com|org|net|io|co|app|gov|edu|ai|dev)\b",
        app_name, re.IGNORECASE
    ):
        url = app_name if app_name.startswith("http") else "https://" + app_name
        print(f"[AppOpener] Web -> '{url}'", flush=True)
        webbrowser.open(url)
        return True

    # User folder shorthand -> open_folder
    clean = app_name.lower().strip()
    if "folder" in clean or any(
        f in clean for f in ("download", "desktop", "document", "picture", "photo", "music", "video")
    ):
        ok, _ = open_folder(app_name)
        if ok:
            return True

    # Wait for index then search
    _wait_ready()
    matched_name, path = _find_best_app(app_name)

    if path and os.path.isfile(path):
        try:
            print(f"[AppOpener] '{app_name}' -> '{matched_name}' ({path})", flush=True)
            subprocess.Popen(
                [path], cwd=os.path.dirname(path),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            print(f"[AppOpener] Direct launch failed: {e}", flush=True)

    elif path and path.startswith("uwp:"):
        app_id = path[4:]
        print(f"[AppOpener] UWP '{app_name}' -> shell:AppsFolder\\{app_id}", flush=True)
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            print(f"[AppOpener] UWP launch failed: {e}", flush=True)

    # Try Windows App URI protocols dynamically (e.g. whatsapp:, spotify:, slack:, etc.)
    proto_slug = re.sub(r"[^a-zA-Z0-9_-]", "", clean)
    if proto_slug:
        for proto in (f"{proto_slug}:", f"ms-{proto_slug}:"):
            try:
                os.startfile(proto)
                print(f"[AppOpener] Launched via Windows protocol '{proto}'", flush=True)
                return True
            except Exception:
                pass

    # Direct Windows shell execution
    try:
        os.startfile(clean)
        print(f"[AppOpener] Launched via os.startfile('{clean}')", flush=True)
        return True
    except Exception:
        pass

    # PowerShell Start-Process fallback
    safe = app_name.replace("'", "''")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"Start-Process '{safe}'"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    print(f"[AppOpener] App '{app_name}' not found or could not be launched.", flush=True)
    return False


def find_files(query: str, max_results: int = 5) -> Tuple[List[str], str]:
    """
    Search for files matching the query string, system-wide.
    Uses Windows Search Indexer first; falls back to dynamic os.walk.

    Returns:
        (matching_file_paths: List[str], spoken_summary: str)
    """
    if not query:
        return [], "Please specify a file name or topic to search for."

    clean_q = _clean_file_query(query.lower().strip()) or query.lower().strip()
    print(f"[FileSearch] Query: '{clean_q}' (raw: '{query}')", flush=True)

    # 1. Windows Search Indexer — uses cleaned query, meaningful tokens only
    matches = _indexer_search(clean_q, max_results=25)
    seen    = {m[2] for m in matches}

    # 2. Walk fallback — uses only meaningful keywords (filters stopwords)
    if not matches:
        keywords = _extract_keywords(clean_q)
        print(f"[FileSearch] Walk fallback keywords: {keywords}", flush=True)
        matches  = _walk_search(keywords, seen, max_results=50)

    if not matches:
        return [], f"I couldn't find any files matching '{query}'."

    matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top = matches[:max_results]

    spoken = (
        f"Found {len(matches)} matching file{'s' if len(matches) > 1 else ''}. "
        f"The most relevant is {top[0][3]}."
    )
    return [m[2] for m in top], spoken


def open_file_or_location(file_path: str) -> bool:
    """Open a file with its default Windows app, or reveal it in Explorer."""
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        print(f"[FileFinder] Opening '{file_path}'", flush=True)
        os.startfile(file_path)
        return True
    except Exception as e:
        print(f"[FileFinder] Error: {e}", flush=True)
        try:
            subprocess.Popen(["explorer.exe", "/select,", file_path])
            return True
        except Exception:
            return False
