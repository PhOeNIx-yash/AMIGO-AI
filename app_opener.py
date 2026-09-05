"""
app_opener.py — Simplified App Launcher + File Finder for Amigo Voice Assistant.
Clean, readable, and dynamic system indexing without hardcoding.
"""

import os
import re
import glob
import json
import time
import winreg
import threading
import subprocess
import webbrowser
import shutil
from typing import Dict, List, Optional, Tuple


_USER_HOME = os.path.expanduser("~")

_NOISE_WORDS = frozenset({
    "app", "application", "program", "software", "the", "open", "launch", "start", "me", "a", "an",
})

_SKIP_EXE = frozenset({
    "uninstall", "uninst", "setup", "installer", "updater", "update",
    "crash", "report", "helper", "service", "agent", "squirrel",
})

_SKIP_DIRS = frozenset({
    "windows", "system32", "syswow64", "winsxs", "drivers",
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "temp", "tmp", "cache", "logs", "appdata", "programdata",
})

_CLEAN_WORDS = frozenset({
    "can", "could", "would", "please", "find", "search", "locate", "look", "show", "get", "open", "where",
    "file", "files", "document", "documents", "name", "named", "called", "me", "my", "your", "a", "an", "the",
    "for", "in", "on", "at", "to", "from", "with", "about", "of", "by", "is", "was", "are",
})

_app_dict: Dict[str, str] = {}
_index_built: bool = False
_build_lock = threading.Lock()
_build_thread: Optional[threading.Thread] = None


# ---------------------------------------------------------------------------
# Dynamic Drive & Directory Discovery
# ---------------------------------------------------------------------------

def _get_all_drives() -> List[str]:
    """Discovers all mounted drive letters dynamically."""
    drives = []
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.kernel32.GetLogicalDriveStringsW(len(buf), buf)
        drives = [d for d in buf.value.split("\x00") if d and os.path.isdir(d)]
    except Exception:
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            p = f"{letter}:\\"
            if os.path.isdir(p):
                drives.append(p)
    return drives or ["C:\\"]


def _start_menu_dirs() -> List[str]:
    """Start Menu and Desktop program directories."""
    dirs = []
    for var in ("%APPDATA%", "%ProgramData%"):
        p = os.path.join(os.path.expandvars(var), "Microsoft", "Windows", "Start Menu", "Programs")
        if os.path.isdir(p):
            dirs.append(p)
    for var in ("%USERPROFILE%", "%PUBLIC%"):
        p = os.path.join(os.path.expandvars(var), "Desktop")
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


# ---------------------------------------------------------------------------
# App Index Discovery Sources
# ---------------------------------------------------------------------------

def _src_start_apps() -> Dict[str, str]:
    """Queries Windows StartApps (UWP + Win32 store apps)."""
    out = {}
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            timeout=10, stderr=subprocess.DEVNULL,
        ).decode(errors="replace").strip()
        apps = json.loads(raw)
        if isinstance(apps, dict):
            apps = [apps]
        for app in apps:
            name = app.get("Name", "").lower().strip()
            app_id = app.get("AppID", "").strip()
            if name and app_id:
                out[name] = f"uwp:{app_id}" if "!" in app_id or app_id.startswith(("Microsoft.", "windows.")) else app_id
    except Exception:
        pass
    return out


def _src_app_paths_registry() -> Dict[str, str]:
    """Queries Windows HKLM App Paths registry."""
    out = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
        i = 0
        while True:
            try:
                exe_name = winreg.EnumKey(root, i)
                i += 1
                sub = winreg.OpenKey(root, exe_name)
                path_val, _ = winreg.QueryValueEx(sub, "")
                sub.Close()
                path_val = os.path.expandvars(path_val.strip().strip('"'))
                if path_val and os.path.isfile(path_val):
                    out[os.path.splitext(exe_name)[0].lower().strip()] = path_val
            except OSError:
                break
            except Exception:
                pass
        root.Close()
    except Exception:
        pass
    return out


def _src_lnk_shortcuts() -> Dict[str, str]:
    """Scans Start Menu & Desktop shortcuts (.lnk)."""
    out = {}
    for base in _start_menu_dirs():
        for lnk in glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
            try:
                name = os.path.splitext(os.path.basename(lnk))[0].lower().strip()
                if name and not any(kw in name for kw in _SKIP_EXE):
                    out[name] = lnk
            except Exception:
                pass
    return out


def _src_uninstall_registry() -> Dict[str, str]:
    """Queries Windows Uninstall registry (both HKCU and HKLM, 64-bit and 32-bit)."""
    out = {}
    hives = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subpath in hives:
        try:
            root = winreg.OpenKey(hive, subpath)
            num_subkeys = winreg.QueryInfoKey(root)[0]
            for i in range(num_subkeys):
                try:
                    kname = winreg.EnumKey(root, i)
                    sub = winreg.OpenKey(root, kname)
                    display_name = ""
                    icon_path = ""
                    install_loc = ""
                    try:
                        display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                    except Exception:
                        pass
                    try:
                        icon_path, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                    except Exception:
                        pass
                    try:
                        install_loc, _ = winreg.QueryValueEx(sub, "InstallLocation")
                    except Exception:
                        pass
                    sub.Close()

                    clean_name = str(display_name).lower().strip()
                    if clean_name and not any(kw in clean_name for kw in _SKIP_EXE):
                        if icon_path:
                            target_exe = icon_path.split(",")[0].strip('"').strip()
                            if target_exe.lower().endswith(".exe") and os.path.isfile(target_exe):
                                out[clean_name] = target_exe
                                simple = re.sub(r"\b(?:workplace|desktop|client|app|edition|community|professional)\b", "", clean_name).strip()
                                if simple and simple != clean_name:
                                    out[simple] = target_exe
                        elif install_loc and os.path.isdir(install_loc):
                            for exe in glob.glob(os.path.join(install_loc, "*.exe")) + glob.glob(os.path.join(install_loc, "bin", "*.exe")):
                                stem = os.path.splitext(os.path.basename(exe))[0].lower()
                                if not any(kw in stem for kw in _SKIP_EXE):
                                    out[clean_name] = exe
                                    break
                except Exception:
                    pass
            root.Close()
        except Exception:
            pass
    return out


def _src_common_exes() -> Dict[str, str]:
    """Scans common program locations for .exe files."""
    out = {}
    scan_bases = []
    for drive in _get_all_drives():
        for pf in ("Program Files", "Program Files (x86)"):
            p = os.path.join(drive, pf)
            if os.path.isdir(p):
                scan_bases.append((p, 2))
    local_app = os.environ.get("LOCALAPPDATA", "")
    app_data = os.environ.get("APPDATA", "")
    if local_app:
        scan_bases.append((os.path.join(local_app, "Programs"), 2))
        scan_bases.append((os.path.join(local_app, "Zoom", "bin"), 1))
        scan_bases.append((os.path.join(local_app, "Microsoft", "WindowsApps"), 1))
    if app_data:
        scan_bases.append((os.path.join(app_data, "Zoom", "bin"), 1))
        scan_bases.append((os.path.join(app_data, "Programs"), 2))

    for base, max_depth in scan_bases:
        if not os.path.isdir(base):
            continue
        try:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
                rel_depth = len(os.path.relpath(root, base).split(os.sep))
                if rel_depth > max_depth:
                    dirs.clear()
                    continue
                for f in files:
                    if f.lower().endswith(".exe"):
                        stem = os.path.splitext(f)[0].lower()
                        if not any(kw in stem for kw in _SKIP_EXE) and stem not in out:
                            out[stem] = os.path.join(root, f)
        except Exception:
            pass
    return out


def _build_index() -> None:
    """Builds the in-memory app lookup dictionary."""
    global _app_dict, _index_built
    t0 = time.time()
    idx = {}
    idx.update(_src_common_exes())
    idx.update(_src_uninstall_registry())
    idx.update(_src_app_paths_registry())
    idx.update(_src_lnk_shortcuts())
    idx.update(_src_start_apps())

    with _build_lock:
        _app_dict = idx
        _index_built = True
    print(f"[AppIndex] Ready — {len(_app_dict)} apps indexed in {time.time()-t0:.1f}s", flush=True)



def ensure_built() -> None:
    """Starts background index build if not already running."""
    global _build_thread
    with _build_lock:
        if _index_built or (_build_thread and _build_thread.is_alive()):
            return
        _build_thread = threading.Thread(target=_build_index, daemon=True, name="AppIndex-Build")
        _build_thread.start()


def refresh_app_index() -> None:
    """Forces an app index rebuild."""
    global _index_built
    with _build_lock:
        _index_built = False
    ensure_built()


def _normalize(name: str) -> str:
    return " ".join(t for t in name.lower().split() if t not in _NOISE_WORDS).strip()


def _find_best_app(query: str) -> Tuple[Optional[str], Optional[str]]:
    """Clean, straightforward matching against installed apps."""
    norm_q = _normalize(query)
    if not norm_q:
        return None, None

    # 1. Exact match
    if norm_q in _app_dict:
        return norm_q, _app_dict[norm_q]

    # 2. Substring match
    for name, path in _app_dict.items():
        if norm_q == name or (len(norm_q) >= 3 and (norm_q in name or name in norm_q)):
            return name, path

    return None, None


# ---------------------------------------------------------------------------
# File Search Engine
# ---------------------------------------------------------------------------

_RE_FILE_NON_WORD = re.compile(r"[^\w\s.-]")


def _clean_file_keywords(query: str) -> List[str]:
    clean = _RE_FILE_NON_WORD.sub(" ", query.lower()).strip()
    return [t for t in clean.split() if t not in _CLEAN_WORDS and len(t) >= 2] or clean.split()



def _score_file(path: str, filename: str, keywords: List[str]) -> int:
    p_lower = path.lower()
    f_lower = filename.lower()

    if any(p_lower.endswith(ext) for ext in (".dll", ".sys", ".tmp", ".pyc", ".log", ".dat")):
        return -1000

    score = 0
    if p_lower.startswith(_USER_HOME.lower()):
        score += 400

    matches = sum(1 for kw in keywords if kw in f_lower)
    if not matches and keywords:
        return -1000
    score += matches * 300
    return score


def _search_files_fallback(keywords: List[str], max_results: int = 10) -> List[str]:
    results = []
    roots = [_USER_HOME]
    for d in _get_all_drives():
        if d not in roots and os.path.isdir(d):
            roots.append(d)

    seen = set()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$")) and d.lower() not in _SKIP_DIRS]
            rel_depth = len(os.path.relpath(dirpath, root).split(os.sep))
            if rel_depth > 4:
                dirnames.clear()
                continue
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if full in seen:
                    continue
                score = _score_file(full, fname, keywords)
                if score > 0:
                    results.append((score, os.path.getmtime(full) if os.path.exists(full) else 0, full))
                    seen.add(full)
                    if len(results) >= max_results * 3:
                        break
        if len(results) >= max_results * 3:
            break

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r[2] for r in results[:max_results]]


def find_files(query: str, max_results: int = 5) -> Tuple[List[str], str]:
    """Searches for files matching the given query."""
    keywords = _clean_file_keywords(query)
    if not keywords:
        return [], "Please specify a file name to search for."

    files = _search_files_fallback(keywords, max_results=max_results)
    if not files:
        return [], f"I couldn't find any files matching '{query}'."

    top_name = os.path.basename(files[0])
    summary = f"Found {len(files)} matching file{'s' if len(files) > 1 else ''}. The most relevant is {top_name}."
    return files, summary


# ---------------------------------------------------------------------------
# Folder & App Openers
# ---------------------------------------------------------------------------

def open_folder(folder_name: str) -> Tuple[bool, str]:
    """Opens a user folder or File Explorer in Windows with OneDrive path resolution."""
    raw = (folder_name or "").lower().strip()
    clean = re.sub(r"\b(?:folder|directory|my|open|show)\b", "", raw).strip()

    # 1. File Explorer / Windows Explorer / This PC
    if clean in ("windows explorer", "file explorer", "explorer", "this pc", "my computer", "pc", "") or "explorer" in raw:
        try:
            subprocess.Popen(["explorer.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "Opening File Explorer."
        except Exception as e:
            return False, f"Could not launch File Explorer: {e}"

    home = os.path.expanduser("~")
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or os.path.join(home, "OneDrive")

    # Resolve paths with OneDrive fallback
    candidates_map = {
        "desktop": [
            os.path.join(onedrive, "Desktop") if onedrive else "",
            os.path.join(home, "Desktop"),
            os.path.join(home, "OneDrive", "Desktop"),
        ],
        "documents": [
            os.path.join(onedrive, "Documents") if onedrive else "",
            os.path.join(home, "Documents"),
            os.path.join(home, "OneDrive", "Documents"),
        ],
        "pictures": [
            os.path.join(onedrive, "Pictures") if onedrive else "",
            os.path.join(home, "Pictures"),
            os.path.join(home, "OneDrive", "Pictures"),
        ],
        "photos": [
            os.path.join(onedrive, "Pictures") if onedrive else "",
            os.path.join(home, "Pictures"),
        ],
        "downloads": [
            os.path.join(home, "Downloads"),
        ],
        "music": [
            os.path.join(home, "Music"),
        ],
        "videos": [
            os.path.join(home, "Videos"),
        ],
        "home": [
            home,
        ],
    }

    # Find matching category
    matched_paths = []
    matched_key = ""
    for key, paths in candidates_map.items():
        if key in clean or clean in key:
            matched_paths = paths
            matched_key = key
            break

    # If direct path or match
    if matched_paths:
        for p in matched_paths:
            if p and os.path.exists(p):
                os.startfile(p)
                return True, f"Opening your {(clean or matched_key).title()} folder."

    # If user gave an exact custom directory name or path on disk
    if os.path.exists(folder_name):
        try:
            os.startfile(folder_name)
            return True, f"Opening {folder_name}."
        except Exception:
            pass

    # General fallback: launch explorer to home directory
    subprocess.Popen(["explorer.exe", home], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True, "Opening File Explorer."


def open_windows_app(app_name: str) -> bool:
    """Dynamically launches any application, executable, or Windows shell tool without hardcoded lists."""
    if not app_name:
        return False

    clean = app_name.lower().strip()
    if clean.startswith("http://") or clean.startswith("https://"):
        webbrowser.open(clean)
        return True

    if "folder" in clean or any(f in clean for f in ("download", "desktop", "document", "picture", "music", "video")):
        ok, _ = open_folder(app_name)
        if ok:
            return True

    # 1. Dynamic system PATH (resolves control, taskmgr, calc, notepad, cmd, wt, etc. instantly without waiting on index)
    exe_candidate = shutil.which(clean) or shutil.which(clean.replace(" ", "")) or shutil.which(clean.replace(" ", "_"))
    if exe_candidate:
        try:
            subprocess.Popen([exe_candidate], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass

    # 2. Check indexed apps (Start Menu, Registry, User AppData, Program Files)
    ensure_built()
    if not _index_built:
        deadline = time.time() + 0.8
        while not _index_built and time.time() < deadline:
            time.sleep(0.04)

    _, path = _find_best_app(app_name)
    if path:
        try:
            if path.startswith("uwp:"):
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{path[4:]}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            if not os.path.exists(path) and ("!" in path or "." in path):
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{path}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            if path.lower().endswith(".lnk"):
                os.startfile(path)
                return True
            if os.path.exists(path):
                subprocess.Popen([path], cwd=os.path.dirname(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        except Exception:
            try:
                os.startfile(path)
                return True
            except Exception:
                pass

    # 3. Native Windows Shell Start (handles shell namespaces, control applets, ms-settings:, protocol URIs)
    try:
        r = subprocess.run(["cmd.exe", "/c", "start", "", clean], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        if r.returncode == 0:
            return True
    except Exception:
        pass

    return False



def open_file_or_location(file_path: str) -> bool:
    """Opens a file or reveals it in Explorer."""
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        os.startfile(file_path)
        return True
    except Exception:
        try:
            subprocess.Popen(["explorer.exe", "/select,", file_path])
            return True
        except Exception:
            return False


def execute_file_action(file_path: str, action: str = "open") -> Tuple[bool, str]:
    """Executes a file action (open, reveal, copy)."""
    if not file_path or not os.path.exists(file_path):
        return False, "File is not available."

    if action == "reveal":
        subprocess.Popen(["explorer.exe", "/select,", file_path])
        return True, "Revealing file in Explorer."

    if action == "copy":
        copied = False
        try:
            import pyperclip
            pyperclip.copy(file_path)
            copied = True
        except Exception:
            pass
        if not copied:
            try:
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard", "-Value", file_path], timeout=2)
            except Exception:
                pass
        return True, "File path copied to clipboard."

    ok = open_file_or_location(file_path)
    return ok, "Opening file." if ok else "Could not open file."

