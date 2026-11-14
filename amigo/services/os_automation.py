"""
OS automation layer for the Amigo voice assistant (Windows-first).

Design notes
------------
* Zero hardcoded spoken words, English filler phrases, or duplicated URI tables:
  app names resolve dynamically against running processes, executable metadata,
  and window titles (with optional user-editable app_aliases.json), and settings
  URIs resolve dynamically via settings_catalog.json.
* Every public function preserves its exact name and return signature
  (bool for actions, (bool, str) for set_volume / set_brightness,
  dict for get_system_status, dict | None for get_system_media_info).
* Failures are logged and reported through return values, never raised.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import difflib
import functools
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
import winreg

try:
    import pyautogui
except Exception as _gui_err:  # pragma: no cover
    pyautogui = None
    _GUI_IMPORT_ERROR = _gui_err
else:
    _GUI_IMPORT_ERROR = None

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
log = logging.getLogger("os_automation")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[OS Automation] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


# --------------------------------------------------------------------------- #
# CONFIG (overridable via AMIGO_* environment variables)
# --------------------------------------------------------------------------- #
def _env_number(name: str, default, cast=float):
    try:
        return cast(os.environ[name])
    except (KeyError, ValueError):
        return default


FOCUS_SETTLE_DELAY = _env_number("AMIGO_FOCUS_DELAY", 0.1)
PASTE_SETTLE_DELAY = _env_number("AMIGO_PASTE_DELAY", 0.15)
NEW_TAB_DELAY = _env_number("AMIGO_NEW_TAB_DELAY", 0.3)
TYPE_INTERVAL = _env_number("AMIGO_TYPE_INTERVAL", 0.015)
DEFAULT_SCROLL = _env_number("AMIGO_SCROLL_AMOUNT", 600, int)
DEFAULT_VOLUME_STEP = _env_number("AMIGO_VOLUME_STEP", 10, int)
DEFAULT_RESTART_DELAY = _env_number("AMIGO_RESTART_DELAY", 30, int)
CLOSE_GRACE_SECONDS = _env_number("AMIGO_CLOSE_GRACE", 2.0)
FUZZY_CUTOFF = _env_number("AMIGO_FUZZY_CUTOFF", 0.78)
MEDIA_QUERY_TIMEOUT = _env_number("AMIGO_MEDIA_TIMEOUT", 8.0)
SUBPROCESS_TIMEOUT = _env_number("AMIGO_SUBPROCESS_TIMEOUT", 10.0)
PYAUTOGUI_FAILSAFE = os.environ.get("AMIGO_PYAUTOGUI_FAILSAFE", "0") == "1"

MAX_SHUTDOWN_DELAY = 315_360_000

_BASE_DIR = Path(__file__).resolve().parent
MEDIA_SCRIPT = Path(os.environ.get("AMIGO_MEDIA_SCRIPT", _BASE_DIR / "get_media.ps1"))
ALIAS_FILE = Path(os.environ.get("AMIGO_APP_ALIASES", _BASE_DIR / "app_aliases.json"))
CATEGORY_ALIAS_FILE = _BASE_DIR / "system_category_aliases.json"

# Critical OS processes that must never be terminated
_PROTECTED_PROCESSES = frozenset({
    "system", "system idle process", "registry", "smss", "csrss", "wininit",
    "winlogon", "services", "lsass", "svchost", "dwm", "fontdrvhost", "sihost",
    "taskhostw", "ctfmon", "runtimebroker", "searchhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost", "securityhealthservice", "msmpeng",
})
# Shell host processes whose windows may be closed, but whose processes must not be killed
_WINDOW_ONLY_PROCESSES = frozenset({"explorer", "applicationframehost"})
# Desktop / taskbar window classes that must never receive WM_CLOSE
_SHELL_WINDOW_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
})

_KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "windows": "win", "window": "win",
    "super": "win", "escape": "esc", "return": "enter", "del": "delete",
    "pgup": "pageup", "pgdn": "pagedown", "spacebar": "space",
}
_MULTIWORD_KEYS = {
    "page up": "pageup", "page down": "pagedown", "print screen": "printscreen",
    "caps lock": "capslock", "num lock": "numlock", "scroll lock": "scrolllock",
}
_WINDOW_HOTKEYS = {
    "minimize_all": ("win", "d"),
    "maximize": ("win", "up"),
    "restore": ("win", "down"),
    "switch_window": ("alt", "tab"),
}

# Win32 constants
WM_CLOSE = 0x0010
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
E_UNEXPECTED = 0x8000FFFF
ERROR_SHUTDOWN_IN_PROGRESS = 1190
ERROR_NO_SHUTDOWN_IN_PROGRESS = 1116

_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if pyautogui is not None:
    pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _gui():
    if pyautogui is None:
        raise RuntimeError(f"pyautogui is unavailable: {_GUI_IMPORT_ERROR}")
    return pyautogui


def _require_windows():
    if not _IS_WINDOWS:
        raise OSError("this action is only supported on Windows")


def _guarded(label: str, default=False):
    """Log-and-return-default wrapper so no automation call can crash the assistant."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log.error("Error during %s: %s", label, exc)
                return default
        return wrapper
    return deco


def _to_int(value, default=None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_percent(level) -> int:
    if isinstance(level, (int, float)):
        value = float(level)
    else:
        m = re.search(r"-?\d+(?:\.\d+)?", str(level))
        if not m:
            raise ValueError(f"no number found in {level!r}")
        value = float(m.group())
    return max(0, min(100, int(round(value))))


def _system_exe(name: str) -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if root:
        candidate = Path(root) / "System32" / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which(name) or name


def _powershell() -> str:
    return shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def _run_hidden(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout or SUBPROCESS_TIMEOUT, creationflags=_NO_WINDOW,
    )


# --- Private ctypes Win32 bindings -----------------------------------------
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32_cache = None


def _user32():
    global _user32_cache
    _require_windows()
    if _user32_cache is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindow.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        u.SendInput.restype = wintypes.UINT
        u.LockWorkStation.restype = wintypes.BOOL
        u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
        u._enum_proc_type = enum_proc
        _user32_cache = u
    return _user32_cache


def _window_info(hwnd) -> tuple[int, str, str]:
    """-> (pid, title, window_class) for a window handle."""
    u = _user32()
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    n = u.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, title, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, cls, 256)
    return pid.value, title.value, cls.value


def _enum_visible_windows() -> list[tuple[int, int, str, str]]:
    """Visible, titled, non-shell top-level windows: (hwnd, pid, title, class)."""
    u = _user32()
    found = []

    def _cb(hwnd, _lparam):
        if u.IsWindowVisible(hwnd):
            pid, title, cls = _window_info(hwnd)
            if title and cls not in _SHELL_WINDOW_CLASSES:
                found.append((hwnd, pid, title, cls))
        return True

    u.EnumWindows(u._enum_proc_type(_cb), 0)
    return found


def _protected_pids() -> set[int]:
    """The assistant process itself plus the parent chain that launched it."""
    import psutil
    pids = {os.getpid()}
    try:
        for parent in psutil.Process().parents():
            if re.sub(r"\.exe$", "", parent.name().lower()) in _WINDOW_ONLY_PROCESSES:
                break
            pids.add(parent.pid)
    except Exception:
        pass
    return pids


def _post_close(hwnds) -> None:
    u = _user32()
    for h in hwnds:
        u.PostMessageW(h, WM_CLOSE, 0, 0)


def _wait_windows_gone(hwnds, timeout: float) -> list:
    """Polls until windows disappear; returns the handles still alive."""
    u = _user32()
    deadline = time.monotonic() + timeout
    alive = list(hwnds)
    while alive and time.monotonic() < deadline:
        alive = [h for h in alive if u.IsWindow(h)]
        if alive:
            time.sleep(0.05)
    return [h for h in alive if u.IsWindow(h)]


def _press_media_key(vk: int, gui_name: str) -> None:
    if _IS_WINDOWS:
        u = _user32()
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    else:
        _gui().press(gui_name)


# --------------------------------------------------------------------------- #
# Typing & Clipboard Injection
# --------------------------------------------------------------------------- #
def _send_unicode(text: str) -> None:
    """Types arbitrary Unicode text (Indic scripts, emoji, accents, symbols) via SendInput."""
    u = _user32()
    gui = _gui()
    for segment in re.split(r"(\n|\t)", text.replace("\r\n", "\n")):
        if segment == "\n":
            gui.press("enter")
            continue
        if segment == "\t":
            gui.press("tab")
            continue
        data = segment.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        for start in range(0, len(units), 64):
            events = []
            for code in units[start:start + 64]:
                for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                    ev = _INPUT(type=INPUT_KEYBOARD)
                    ev.u.ki = _KEYBDINPUT(0, code, flags, 0, 0)
                    events.append(ev)
            arr = (_INPUT * len(events))(*events)
            sent = u.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
            if sent != len(events):
                raise OSError(
                    "SendInput was blocked (target window may be elevated)"
                )
            time.sleep(TYPE_INTERVAL / 3)


def _type_literal(text: str) -> None:
    if _IS_WINDOWS:
        _send_unicode(text)
        return
    if not text.isascii():
        log.warning("Non-ASCII text cannot be typed without clipboard support; characters may be lost")
    _gui().write(text, interval=TYPE_INTERVAL)


def _paste_text(text: str) -> bool:
    """Paste via clipboard, restoring previous text clipboard afterwards."""
    try:
        import pyperclip
    except ImportError:
        return False
    try:
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        pyperclip.copy(text)
        _gui().hotkey("ctrl", "v")
        time.sleep(PASTE_SETTLE_DELAY)
        if previous:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass
        return True
    except Exception as exc:
        log.warning("Clipboard paste failed (%s); falling back to typing", exc)
        return False


@_guarded("typing text")
def type_text(text):
    """Types or pastes text into the active window/application."""
    if not text:
        return False
    text = str(text)
    time.sleep(FOCUS_SETTLE_DELAY)
    if _paste_text(text):
        log.info("Pasted text into active window (%d chars)", len(text))
    else:
        _type_literal(text)
        log.info("Typed text (%d chars)", len(text))
    return True


@_guarded("search_and_type")
def search_and_type(text):
    """Types text into the active input field/search bar and presses Enter."""
    if not text:
        return False
    time.sleep(FOCUS_SETTLE_DELAY)
    _type_literal(str(text))
    _gui().press("enter")
    log.info("Searched & typed: %s", text)
    return True


# --------------------------------------------------------------------------- #
# Scrolling & Browser Tabs
# --------------------------------------------------------------------------- #
@_guarded("scrolling down")
def scroll_down(amount=DEFAULT_SCROLL):
    """Scrolls down on the current webpage or document."""
    n = _to_int(amount, DEFAULT_SCROLL)
    _gui().scroll(-abs(n))
    log.info("Scrolled down by %s", abs(n))
    return True


@_guarded("scrolling up")
def scroll_up(amount=DEFAULT_SCROLL):
    """Scrolls up on the current webpage or document."""
    n = _to_int(amount, DEFAULT_SCROLL)
    _gui().scroll(abs(n))
    log.info("Scrolled up by %s", abs(n))
    return True


@_guarded("opening new tab")
def new_tab(url=""):
    """Opens a new browser tab (ctrl+t) and optionally navigates to a URL."""
    gui = _gui()
    gui.hotkey("ctrl", "t")
    if url:
        time.sleep(NEW_TAB_DELAY)
        gui.hotkey("ctrl", "l")
        if not _paste_text(str(url)):
            _type_literal(str(url))
        gui.press("enter")
    log.info("Opened new tab: %s", url)
    return True


@_guarded("closing tab")
def close_tab():
    """Closes the current browser tab (ctrl+w)."""
    _gui().hotkey("ctrl", "w")
    log.info("Closed browser tab")
    return True


@_guarded("switching tab")
def next_tab():
    """Switches to the next browser tab (ctrl+tab)."""
    _gui().hotkey("ctrl", "tab")
    log.info("Switched to next tab")
    return True


@_guarded("switching tab")
def prev_tab():
    """Switches to the previous browser tab (ctrl+shift+tab)."""
    _gui().hotkey("ctrl", "shift", "tab")
    log.info("Switched to previous tab")
    return True


# --------------------------------------------------------------------------- #
# Shortcuts & Window Management
# --------------------------------------------------------------------------- #
def _normalize_keys(keys) -> list[str]:
    if isinstance(keys, (list, tuple)):
        parts = [str(k) for k in keys]
    else:
        text = str(keys).strip().lower()
        for phrase, key in _MULTIWORD_KEYS.items():
            text = text.replace(phrase, key)
        text = re.sub(r"\b(?:up|down|left|right)\s+arrow\b", lambda m: m.group().split()[0], text)
        parts = re.split(r"\s*\+\s*|\s+plus\s+|[\s,]+", text)
    out = []
    for p in parts:
        p = p.strip().lower()
        if p and p != "arrow":
            out.append(_KEY_ALIASES.get(p, p))
    return out


@_guarded("pressing shortcut")
def press_shortcut(keys):
    """Presses a combination of keys (e.g. 'ctrl+c', 'alt+tab')."""
    gui = _gui()
    key_list = _normalize_keys(keys)
    if not key_list:
        log.warning("Empty shortcut: %r", keys)
        return False
    valid = set(gui.KEYBOARD_KEYS)
    unknown = [k for k in key_list if k not in valid]
    if unknown:
        log.warning("Unknown key(s) %s in shortcut %r", unknown, keys)
        return False
    time.sleep(FOCUS_SETTLE_DELAY)
    try:
        gui.hotkey(*key_list)
    except Exception:
        for k in reversed(key_list):
            try:
                gui.keyUp(k)
            except Exception:
                pass
        raise
    log.info("Pressed shortcut: %s", "+".join(key_list))
    return True


def _close_foreground_window() -> bool:
    """Gracefully closes the focused window, refusing desktop/taskbar/protected assistant processes."""
    _require_windows()
    u = _user32()
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        log.warning("No foreground window to close")
        return False
    pid, title, cls = _window_info(hwnd)
    if cls in _SHELL_WINDOW_CLASSES:
        log.warning("Foreground window is the desktop/taskbar; not closing it")
        return False
    if pid in _protected_pids():
        log.warning("Foreground window belongs to protected assistant process; not closing it")
        return False
    u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    log.info("Closed current window: %r", title)
    return True


@_guarded("window action")
def window_action(action):
    """Performs window management actions."""
    name = re.sub(r"[\s-]+", "_", str(action).strip().lower())
    if name == "close_window":
        return _close_foreground_window()
    keys = _WINDOW_HOTKEYS.get(name)
    if keys is None:
        log.warning("Unknown window action: %s (known: close_window, %s)",
                    action, ", ".join(_WINDOW_HOTKEYS))
        return False
    _gui().hotkey(*keys)
    log.info("Window action: %s", name)
    return True


# --------------------------------------------------------------------------- #
# Closing Applications (Dynamic Process & Window Resolution)
# --------------------------------------------------------------------------- #
def _squash(s: str) -> str:
    return re.sub(r"[^\w]+", "", s.lower(), flags=re.UNICODE)


def _load_aliases() -> dict[str, str]:
    """Optional user-editable {"spoken name": "process or window name"} JSON file."""
    try:
        with open(ALIAS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k).lower().strip(): str(v).lower().strip() for k, v in data.items()}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("Could not read alias file %s: %s", ALIAS_FILE, exc)
        return {}


def _normalize_app_name(app_name: str) -> str:
    raw = (app_name or "").strip().rstrip(".!?,;:")
    return re.sub(r"[^\w\s-]", "", raw.lower(), flags=re.UNICODE).strip()


def _close_process_group(procs, windows, *, allow_terminate: bool, force: bool) -> bool:
    """Graceful WM_CLOSE first; only terminates processes that have no remaining visible window (unless force=True)."""
    import psutil

    pids = {p.pid for p in procs}
    hwnds = [h for h, pid, _t, _c in windows if pid in pids]
    if hwnds:
        _post_close(hwnds)
        _wait_windows_gone(hwnds, CLOSE_GRACE_SECONDS)

    def _stem(p):
        try:
            return re.sub(r"\.exe$", "", p.name().lower())
        except Exception:
            return ""

    names = {p.pid: _stem(p) for p in procs}
    killable = [p for p in procs
                if p.is_running() and names.get(p.pid) not in _WINDOW_ONLY_PROCESSES]
    window_only = [p for p in procs if names.get(p.pid) in _WINDOW_ONLY_PROCESSES]
    closed_any = bool(window_only and hwnds and not _wait_windows_gone(hwnds, 0))

    if killable:
        _, alive = psutil.wait_procs(killable, timeout=0.3 if hwnds else 0)
        closed_any = closed_any or len(alive) < len(killable)
        if alive and allow_terminate:
            still_visible = {pid for _h, pid, _t, _c in _enum_visible_windows()}
            to_kill = [p for p in alive if force or p.pid not in still_visible]
            for p in to_kill:
                for child in p.children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            if to_kill:
                psutil.wait_procs(to_kill, timeout=1.0)
                closed_any = True
            held_open = [p for p in alive if p not in to_kill]
            if held_open:
                log.info("Left %d process(es) running: visible dialog/window still open", len(held_open))
        elif alive:
            log.info("%d process(es) did not exit after close request", len(alive))
    return closed_any


@_guarded("closing app")
def close_app(app_name: str = "", force: bool = False) -> bool:
    """
    Closes an application by name, or the active foreground window if no target was named.
    Dynamically resolves against process names, fuzzy windowed process stems, and window titles.
    """
    clean = _normalize_app_name(app_name)
    if not clean:
        return window_action("close_window")

    _require_windows()
    import psutil

    clean = _load_aliases().get(clean, clean)
    protected = _protected_pids()
    windows = _enum_visible_windows()

    procs_by_stem: dict[str, list] = defaultdict(list)
    for p in psutil.process_iter(["pid", "name"]):
        try:
            stem = re.sub(r"\.exe$", "", (p.info["name"] or "").lower())
            if stem and stem not in _PROTECTED_PROCESSES and p.pid not in protected:
                procs_by_stem[stem].append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Tier 1: exact squashed process name ("chrome", "vs code" -> "vscode")
    target = _squash(clean)
    exact = [s for s in procs_by_stem if _squash(s) == target]
    if exact:
        procs = [p for s in exact for p in procs_by_stem[s]]
        if _close_process_group(procs, windows, allow_terminate=True, force=force):
            log.info("Closed '%s' (process match)", clean)
            return True
        return False

    # Tier 2: fuzzy process name among processes owning a visible window (graceful close only)
    windowed = {pid for _h, pid, _t, _c in windows}
    windowed_stems = {
        _squash(s): s for s, ps in procs_by_stem.items()
        if any(p.pid in windowed for p in ps)
    }
    close = difflib.get_close_matches(target, list(windowed_stems), n=1, cutoff=FUZZY_CUTOFF)
    if close:
        stem = windowed_stems[close[0]]
        if _close_process_group(procs_by_stem[stem], windows, allow_terminate=False, force=False):
            log.info("Closed '%s' (fuzzy match: %s)", clean, stem)
            return True
        return False

    # Tier 3: visible window titles containing every requested token (Unicode-aware)
    tokens = [t for t in re.findall(r"\w+", clean.lower(), flags=re.UNICODE) if t]
    title_hits = [
        (h, pid, t) for h, pid, t, _c in windows
        if pid not in protected and tokens and all(tok in t.lower() for tok in tokens)
    ]
    if title_hits:
        hwnds = [h for h, _pid, _t in title_hits]
        _post_close(hwnds)
        remaining = _wait_windows_gone(hwnds, CLOSE_GRACE_SECONDS)
        if len(remaining) < len(hwnds):
            log.info("Closed %d window(s) matching '%s'", len(hwnds) - len(remaining), clean)
            return True
        return False

    log.info("No running app or window matches '%s'", clean)
    return False


# --------------------------------------------------------------------------- #
# Power
# --------------------------------------------------------------------------- #
@_guarded("locking workstation")
def lock_pc():
    """Locks the Windows desktop workstation."""
    if not _user32().LockWorkStation():
        raise OSError(f"LockWorkStation failed (error {ctypes.get_last_error()})")
    log.info("Locked workstation")
    return True


@_guarded("sleeping system")
def sleep_pc():
    """Puts the computer to sleep (suspend) on a non-blocking worker thread."""
    _require_windows()
    powrprof = ctypes.WinDLL("powrprof")
    fn = powrprof.SetSuspendState
    fn.argtypes = [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
    fn.restype = wintypes.BOOLEAN
    result: dict = {}

    def _run():
        result["ok"] = bool(fn(False, False, False))

    t = threading.Thread(target=_run, name="amigo-sleep", daemon=True)
    t.start()
    t.join(1.5)
    if not t.is_alive() and not result.get("ok"):
        raise OSError("SetSuspendState failed (sleep unsupported or blocked by policy)")
    log.info("System sleeping")
    return True


@_guarded("emptying recycle bin")
def empty_recycle_bin():
    """Empties the Windows Recycle Bin silently."""
    _require_windows()
    shell32 = ctypes.WinDLL("shell32")
    fn = shell32.SHEmptyRecycleBinW
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
    fn.restype = ctypes.c_long
    SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 0x1, 0x2, 0x4
    res = fn(None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND) & 0xFFFFFFFF
    if res == 0:
        log.info("Emptied Recycle Bin")
        return True
    if res == E_UNEXPECTED:
        log.info("Recycle Bin was already empty")
        return True
    raise OSError(f"SHEmptyRecycleBinW returned 0x{res:08X}")


@_guarded("restarting system")
def restart_pc(delay=None):
    """Schedules a system restart (default delay allows cancel_shutdown())."""
    _require_windows()
    seconds = _to_int(delay, DEFAULT_RESTART_DELAY) if delay is not None else DEFAULT_RESTART_DELAY
    seconds = max(0, min(MAX_SHUTDOWN_DELAY, seconds))
    exe = _system_exe("shutdown")
    r = _run_hidden([exe, "/r", "/t", str(seconds)])
    if r.returncode == ERROR_SHUTDOWN_IN_PROGRESS:
        _run_hidden([exe, "/a"])
        r = _run_hidden([exe, "/r", "/t", str(seconds)])
    if r.returncode != 0:
        raise OSError(f"shutdown exited {r.returncode}: {(r.stderr or r.stdout).strip()}")
    log.info("Scheduled system restart in %d seconds", seconds)
    return True


@_guarded("cancelling shutdown")
def cancel_shutdown():
    """Cancels a scheduled system shutdown or restart."""
    _require_windows()
    r = _run_hidden([_system_exe("shutdown"), "/a"])
    if r.returncode == ERROR_NO_SHUTDOWN_IN_PROGRESS:
        log.info("No shutdown/restart was scheduled")
        return False
    if r.returncode != 0:
        raise OSError(f"shutdown /a exited {r.returncode}: {(r.stderr or r.stdout).strip()}")
    log.info("Cancelled shutdown/restart schedule")
    return True


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #
@_guarded("skipping track")
def next_track():
    """Skips to the next track/song/video globally."""
    _press_media_key(VK_MEDIA_NEXT, "nexttrack")
    log.info("Skipped to next track")
    return True


@_guarded("going to previous track")
def prev_track():
    """Goes back to the previous track/song/video globally."""
    _press_media_key(VK_MEDIA_PREV, "prevtrack")
    log.info("Went back to previous track")
    return True


@_guarded("toggling play/pause")
def play_pause_media():
    """Toggles play/pause media playback globally."""
    _press_media_key(VK_MEDIA_PLAY_PAUSE, "playpause")
    log.info("Toggled play/pause media")
    return True


_UNAVAILABLE = object()


def _media_info_via_winrt():
    """Fast in-process query of Windows' media session API (winsdk or winrt)."""
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
    except ImportError:
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager,
            )
        except ImportError:
            return _UNAVAILABLE

    async def _query():
        mgr = await Manager.request_async()
        sessions = []
        try:
            curr = mgr.get_current_session()
            if curr:
                sessions.append(curr)
        except Exception:
            pass
        try:
            for s in (mgr.get_sessions() or []):
                if s not in sessions:
                    sessions.append(s)
        except Exception:
            pass

        for session in sessions:
            try:
                props = await session.try_get_media_properties_async()
                if props and props.title:
                    info = session.get_playback_info()
                    status = getattr(info.playback_status, "name", str(info.playback_status)).lower()
                    return {"status": status, "title": props.title, "artist": props.artist or ""}
            except Exception:
                continue
        return None

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(lambda: asyncio.run(_query())).result(timeout=MEDIA_QUERY_TIMEOUT)
    finally:
        pool.shutdown(wait=False)


def _media_info_via_script():
    if not MEDIA_SCRIPT.exists():
        return _UNAVAILABLE
    quoted = str(MEDIA_SCRIPT).replace("'", "''")
    command = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; & '{quoted}'"
    proc = _run_hidden(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        timeout=MEDIA_QUERY_TIMEOUT,
    )
    status = title = artist = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("STATUS:"):
            status = line[7:].strip()
        elif line.startswith("TITLE:"):
            title = line[6:].strip()
        elif line.startswith("ARTIST:"):
            artist = line[7:].strip()
    return {"status": status.lower(), "title": title, "artist": artist}


def _media_info_via_active_audio_window() -> dict | None:
    """Resolves media title from the visible window of any process actively emitting audio."""
    try:
        import pythoncom
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        pythoncom.CoInitialize()
        active_pids: set[int] = set()
        try:
            for s in AudioUtilities.GetAllSessions():
                if s.Process:
                    try:
                        if s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() > 0.0005:
                            active_pids.add(s.Process.pid)
                    except Exception:
                        pass
        finally:
            pythoncom.CoUninitialize()

        if not active_pids:
            return None

        for _, pid, raw_t, _ in _enum_visible_windows():
            if pid in active_pids and raw_t and len(raw_t.strip()) > 1:
                t = raw_t.strip()
                if " - " in t:
                    parts = t.split(" - ")
                    if len(parts) >= 2:
                        return {"status": "playing", "title": " - ".join(parts[:-1]).strip(), "artist": ""}
                return {"status": "playing", "title": t, "artist": ""}
    except Exception:
        pass
    return None


def get_system_media_info() -> dict | None:
    """Real-time media title, artist and playback status from Windows media transport controls."""
    if not _IS_WINDOWS:
        return None
    for source in (_media_info_via_winrt, _media_info_via_script, _media_info_via_active_audio_window):
        try:
            info = source()
        except Exception as exc:
            log.debug("Media query via %s failed: %s", source.__name__, exc)
            continue
        if info is _UNAVAILABLE:
            continue
        if info and info.get("title"):
            return info
    return None


# --------------------------------------------------------------------------- #
# Volume & Brightness
# --------------------------------------------------------------------------- #
def _with_endpoint_volume(fn):
    """Runs fn(IAudioEndpointVolume) with correct COM init/teardown."""
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        if hasattr(device, "EndpointVolume"):
            endpoint = device.EndpointVolume
        else:
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = cast(iface, POINTER(IAudioEndpointVolume))
        try:
            return fn(endpoint)
        finally:
            del endpoint
    finally:
        comtypes.CoUninitialize()


def get_volume() -> int | None:
    """Current master volume 0-100, or None if unreadable."""
    try:
        return int(round(_with_endpoint_volume(lambda ep: ep.GetMasterVolumeLevelScalar()) * 100))
    except Exception as exc:
        log.warning("Could not read volume: %s", exc)
        return None


def _nudge_volume(direction: int, step, vk: int, gui_name: str) -> bool:
    step = max(1, _to_int(step, DEFAULT_VOLUME_STEP))
    current = get_volume()
    if current is not None:
        ok, _msg = set_volume(max(0, min(100, current + direction * step)))
        if ok:
            return True
    for _ in range(max(1, step // 2)):
        _press_media_key(vk, gui_name)
    return True


@_guarded("increasing volume")
def volume_up(step=None):
    """Increases system audio volume (default AMIGO_VOLUME_STEP percent)."""
    _nudge_volume(+1, step if step is not None else DEFAULT_VOLUME_STEP, VK_VOLUME_UP, "volumeup")
    log.info("Volume up")
    return True


@_guarded("decreasing volume")
def volume_down(step=None):
    """Decreases system audio volume (default AMIGO_VOLUME_STEP percent)."""
    _nudge_volume(-1, step if step is not None else DEFAULT_VOLUME_STEP, VK_VOLUME_DOWN, "volumedown")
    log.info("Volume down")
    return True


@_guarded("toggling mute")
def mute(state: bool | None = None):
    """Toggles mute, or sets it explicitly when state is True/False."""
    if state is not None:
        try:
            _with_endpoint_volume(lambda ep: ep.SetMute(1 if state else 0, None))
            log.info("Mute set to %s", state)
            return True
        except Exception as exc:
            log.warning("Explicit mute failed (%s); falling back to toggle", exc)
    _press_media_key(VK_VOLUME_MUTE, "volumemute")
    log.info("Toggled mute")
    return True


def set_volume(level: int | str) -> tuple[bool, str]:
    """Sets master volume (0-100) via Windows Core Audio API and unmutes when > 0."""
    try:
        level_int = _parse_percent(level)

        def _apply(ep):
            ep.SetMasterVolumeLevelScalar(level_int / 100.0, None)
            if level_int > 0:
                try:
                    ep.SetMute(0, None)
                except Exception:
                    pass

        _with_endpoint_volume(_apply)
        return True, f"Volume set to {level_int} percent."
    except Exception as exc:
        log.error("Volume error: %s", exc)
        return False, f"Could not adjust volume to {level} percent."


def get_brightness() -> int | None:
    """Current display brightness 0-100, or None if unavailable."""
    try:
        import screen_brightness_control as sbc
        val = sbc.get_brightness()
        return int(val[0]) if isinstance(val, list) and val else int(val)
    except Exception:
        pass
    try:
        ps_cmd = (
            "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
            "| Select-Object -First 1).CurrentBrightness"
        )
        r = _run_hidden([_powershell(), "-NoProfile", "-Command", ps_cmd])
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except Exception:
        pass
    return None


def set_brightness(level: int | str) -> tuple[bool, str]:
    """Sets monitor brightness (0-100) via screen_brightness_control, else WMI/CIM."""
    try:
        level_int = _parse_percent(level)
    except ValueError as exc:
        log.error("Brightness error: %s", exc)
        return False, f"Could not understand brightness level {level}."

    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(level_int)
        return True, f"Screen brightness set to {level_int} percent."
    except Exception as exc:
        log.info("screen_brightness_control unavailable/failed (%s); trying CIM", exc)

    try:
        ps_cmd = (
            "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
            f"| Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level_int}}}"
        )
        r = _run_hidden([_powershell(), "-NoProfile", "-Command", ps_cmd])
        if r.returncode == 0 and not r.stderr.strip():
            return True, f"Screen brightness set to {level_int} percent."
        log.error("CIM brightness failed: %s", (r.stderr or r.stdout).strip())
    except Exception as exc:
        log.error("Brightness error: %s", exc)
    return False, f"Could not adjust brightness to {level_int} percent."


def get_system_status() -> dict:
    """Real-time CPU, RAM and battery metrics."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        bat_percent = round(battery.percent, 1) if battery else None
        plugged = battery.power_plugged if battery else None

        if bat_percent is None:
            bat_text = "not available on this device"
        else:
            charge = {True: ", charging", False: ", on battery", None: ""}[plugged]
            bat_text = f"{bat_percent:.0f} percent{charge}"
        return {
            "cpu": round(cpu, 1),
            "ram": round(mem.percent, 1),
            "battery": bat_percent,
            "plugged": plugged,
            "summary": (f"CPU is at {cpu:.0f} percent, RAM usage is {mem.percent:.0f} percent, "
                        f"and Battery is {bat_text}."),
        }
    except Exception as exc:
        log.error("System status error: %s", exc)
        return {
            "cpu": None, "ram": None, "battery": None, "plugged": None,
            "summary": "Could not read system hardware status.",
        }


# --------------------------------------------------------------------------- #
# Windows Settings Engine Forwarders (see os_settings.py)
# --------------------------------------------------------------------------- #
try:
    try:
        from amigo.services.os_settings import (
            change_setting, get_setting, toggle_setting, list_settings, reload_catalog,
        )
    except ImportError:
        from os_settings import (
            change_setting, get_setting, toggle_setting, list_settings, reload_catalog,
        )
except Exception as _settings_err:
    log.warning("Settings engine unavailable: %s", _settings_err)

    def change_setting(setting, value=None, confirmed=False):
        return False, "The settings engine isn't available."

    def get_setting(setting):
        return False, "The settings engine isn't available."

    def toggle_setting(setting):
        return False, "The settings engine isn't available."

    def list_settings(query=None):
        return []

    def reload_catalog():
        return 0


# --------------------------------------------------------------------------- #
# Unified Windows System Controller (Zero Duplicate Hardware/URI Tables)
# --------------------------------------------------------------------------- #
class WindowsSystemController:
    """
    Executes native Windows hardware and OS adjustments via structured parameters.
    All methods return standardized dicts for JSON serialization.
    """

    @staticmethod
    def _run_powershell(script: str, timeout: float = 12.0) -> subprocess.CompletedProcess:
        return _run_hidden([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)

    @classmethod
    def get_wifi_status(cls) -> dict[str, Any]:
        ps_script = """
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]
        Function Await($WinRtTask, $ResultType) {
            $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
            $netTask = $asTask.Invoke($null, @($WinRtTask))
            $netTask.Wait(-1) | Out-Null
            $netTask.Result
        }
        [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
        [Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
        Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
        $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        $target = $radios | ? { $_.Kind -eq 'WiFi' }
        $state = if ($target) { $target[0].State.ToString() } else { 'Unknown' }

        [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime] | Out-Null
        $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
        $profileName = if ($profile) { $profile.ProfileName } else { '' }
        $isWlan = if ($profile) { $profile.IsWlanConnectionProfile.ToString() } else { 'False' }

        Write-Output ($state + '|' + $profileName + '|' + $isWlan)
        """
        try:
            res = cls._run_powershell(ps_script)
            parts = res.stdout.strip().split("|")
            state = parts[0].strip() if len(parts) > 0 else "Unknown"
            profile = parts[1].strip() if len(parts) > 1 else ""
            is_wlan = parts[2].strip().lower() == "true" if len(parts) > 2 else False

            is_connected = bool(profile and is_wlan)
            return {
                "status": "success",
                "radio_state": state,
                "connected": is_connected,
                "profile": profile,
                "is_wlan": is_wlan,
            }
        except Exception as exc:
            return {
                "status": "error",
                "code": "WIFI_QUERY_FAILED",
                "details": str(exc),
                "radio_state": "Unknown",
                "connected": False,
                "profile": "",
            }

    @classmethod
    def get_radio_state(cls, radio_type: str) -> dict[str, Any]:
        valid_radios = {"wifi": "WiFi", "bluetooth": "Bluetooth"}
        target_kind = valid_radios.get(radio_type.lower())
        if not target_kind:
            return {"status": "error", "code": "INVALID_RADIO_TYPE", "supported": list(valid_radios.keys())}

        ps_script = f"""
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        }})[0]
        Function Await($WinRtTask, $ResultType) {{
            $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
            $netTask = $asTask.Invoke($null, @($WinRtTask))
            $netTask.Wait(-1) | Out-Null
            $netTask.Result
        }}
        [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
        [Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
        Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
        $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        $target = $radios | ? {{ $_.Kind -eq '{target_kind}' }}
        if ($target) {{
            Write-Output $target[0].State.ToString()
        }} else {{
            Write-Output "NOT_FOUND"
        }}
        """
        try:
            res = cls._run_powershell(ps_script)
            out = res.stdout.strip()
            if "On" in out:
                return {"status": "success", "component": target_kind, "state": "On", "enabled": True}
            if "Off" in out:
                return {"status": "success", "component": target_kind, "state": "Off", "enabled": False}
            if "NOT_FOUND" in out:
                return {"status": "error", "code": "HARDWARE_NOT_FOUND", "component": target_kind}
            return {"status": "success", "component": target_kind, "state": out, "enabled": out.lower() == "on"}
        except Exception as exc:
            return {"status": "error", "code": "QUERY_FAILED", "details": str(exc)}

    @classmethod
    def set_radio_state(cls, radio_type: str, enabled: bool) -> dict[str, Any]:
        valid_radios = {"wifi": "WiFi", "bluetooth": "Bluetooth"}
        target_kind = valid_radios.get(radio_type.lower())
        if not target_kind:
            return {"status": "error", "code": "INVALID_RADIO_TYPE", "supported": list(valid_radios.keys())}

        if target_kind == "WiFi":
            wifi_info = cls.get_wifi_status()
            current_state = wifi_info.get("radio_state", "Unknown")
            is_connected = wifi_info.get("connected", False)
            profile_name = wifi_info.get("profile", "")

            if enabled and current_state == "On":
                if is_connected and profile_name:
                    return {
                        "status": "already_connected",
                        "component": "WiFi",
                        "enabled": True,
                        "connected": True,
                        "profile": profile_name,
                        "message": f"Wi-Fi is already turned on and connected to {profile_name}.",
                    }
                return {
                    "status": "already_on",
                    "component": "WiFi",
                    "enabled": True,
                    "connected": False,
                    "profile": profile_name,
                    "message": "Wi-Fi is already turned on.",
                }
            if not enabled and current_state == "Off":
                return {
                    "status": "already_off",
                    "component": "WiFi",
                    "enabled": False,
                    "message": "Wi-Fi is already turned off.",
                }
        elif target_kind == "Bluetooth":
            bt_info = cls.get_radio_state("bluetooth")
            if bt_info.get("status") == "success":
                bt_on = bt_info.get("enabled", False)
                if enabled and bt_on:
                    return {
                        "status": "already_on",
                        "component": "Bluetooth",
                        "enabled": True,
                        "message": "Bluetooth is already turned on.",
                    }
                if not enabled and not bt_on:
                    return {
                        "status": "already_off",
                        "component": "Bluetooth",
                        "enabled": False,
                        "message": "Bluetooth is already turned off.",
                    }

        target_state = "On" if enabled else "Off"
        ps_script = f"""
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        }})[0]
        Function Await($WinRtTask, $ResultType) {{
            $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
            $netTask = $asTask.Invoke($null, @($WinRtTask))
            $netTask.Wait(-1) | Out-Null
            $netTask.Result
        }}
        [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
        [Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
        Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
        $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        $target = $radios | ? {{ $_.Kind -eq '{target_kind}' }}
        if ($target) {{
            foreach ($r in $target) {{
                Await ($r.SetStateAsync('{target_state}')) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
            }}
            Write-Output "SUCCESS"
        }} else {{
            Write-Output "NOT_FOUND"
        }}
        """
        try:
            res = cls._run_powershell(ps_script)
            if "SUCCESS" in res.stdout:
                action_text = "turned on" if enabled else "turned off"
                return {
                    "status": "success",
                    "component": target_kind,
                    "enabled": enabled,
                    "message": f"{target_kind} is now {action_text}.",
                }
            return {
                "status": "error",
                "code": "HARDWARE_NOT_FOUND",
                "details": res.stderr.strip() or res.stdout.strip(),
                "message": f"Could not find {target_kind} radio on this device.",
            }
        except Exception as e:
            return {"status": "error", "code": "EXECUTION_FAILED", "details": str(e)}

    @staticmethod
    def get_color_theme() -> dict[str, Any]:
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ)
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return {"status": "success", "component": "theme", "mode": "light" if val == 1 else "dark"}
        except Exception as exc:
            return {"status": "error", "code": "REGISTRY_READ_FAILED", "details": str(exc)}

    @staticmethod
    def set_color_theme(mode: str) -> dict[str, Any]:
        m = mode.lower().strip()
        if m not in ("dark", "light"):
            return {"status": "error", "code": "INVALID_MODE", "supported": ["dark", "light"]}

        val = 0 if m == "dark" else 1
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, val)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)
            winreg.CloseKey(key)

            result = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 2000, ctypes.byref(result)
            )
            return {"status": "success", "component": "theme", "mode": m, "message": f"Theme switched to {m} mode."}
        except Exception as e:
            return {"status": "error", "code": "REGISTRY_ERROR", "details": str(e)}

    @staticmethod
    def get_audio() -> dict[str, Any]:
        level = get_volume()
        if level is None:
            return {"status": "error", "code": "AUDIO_ERROR"}
        return {"status": "success", "component": "audio", "volume_level": level}

    @staticmethod
    def set_audio(level: int | None = None, mute_state: bool | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {}
        msg_parts = []
        if mute_state is not None:
            if not mute(bool(mute_state)):
                return {"status": "error", "code": "AUDIO_ERROR"}
            state["muted"] = bool(mute_state)
            msg_parts.append("muted" if mute_state else "unmuted")
        if level is not None:
            ok, msg = set_volume(level)
            if not ok:
                return {"status": "error", "code": "AUDIO_ERROR", "message": msg}
            state["volume_level"] = _parse_percent(level)
            msg_parts.append(msg.rstrip("."))
        return {
            "status": "success",
            "component": "audio",
            **state,
            "message": " and ".join(msg_parts).capitalize() + "." if msg_parts else "Audio settings updated.",
        }

    @staticmethod
    def get_brightness() -> dict[str, Any]:
        level = get_brightness()
        if level is None:
            return {"status": "error", "code": "BRIGHTNESS_UNAVAILABLE"}
        return {"status": "success", "component": "brightness", "level": level}

    @staticmethod
    def set_brightness(level: int) -> dict[str, Any]:
        ok, msg = set_brightness(level)
        if not ok:
            return {"status": "error", "code": "BRIGHTNESS_ERROR", "message": msg}
        return {"status": "success", "component": "brightness", "level": _parse_percent(level), "message": msg}

    @staticmethod
    def open_settings_page(uri_target: str) -> dict[str, Any]:
        """Dynamically resolves settings page URI via settings_catalog.json (zero hardcoded maps)."""
        try:
            from amigo.utils.settings_resolver import resolve_setting
        except ImportError:
            from settings_resolver import resolve_setting

        raw = str(uri_target or "").strip()
        if raw.lower().startswith("ms-settings:"):
            label, uri = raw, raw
        else:
            label, uri = resolve_setting(raw)
        try:
            os.startfile(uri)
            return {"status": "success", "opened_uri": uri, "message": f"Opened {label} settings."}
        except Exception as e:
            return {"status": "error", "code": "URI_OPEN_FAILED", "details": str(e)}

    @staticmethod
    def system_power(action: str) -> dict[str, Any]:
        act = action.lower().strip()
        dispatch = {
            "lock": lock_pc,
            "sleep": sleep_pc,
            "restart": restart_pc,
        }
        if act in dispatch:
            ok = dispatch[act]()
            return (
                {"status": "success", "power_action": act, "message": f"Executing system {act}."}
                if ok
                else {"status": "error", "code": "POWER_ACTION_FAILED", "power_action": act}
            )
        if act == "shutdown":
            _require_windows()
            r = _run_hidden([_system_exe("shutdown"), "/s", "/t", "5"])
            if r.returncode == 0:
                return {"status": "success", "power_action": act, "message": "Shutting down in 5 seconds."}
            return {"status": "error", "code": "POWER_ACTION_FAILED", "details": r.stderr.strip()}
        return {
            "status": "error",
            "code": "INVALID_POWER_ACTION",
            "supported": ["lock", "sleep", "shutdown", "restart"],
        }


_CATEGORY_ALIASES_CACHE: dict[str, Any] | None = None


def _get_category_aliases() -> dict[str, Any]:
    global _CATEGORY_ALIASES_CACHE
    if _CATEGORY_ALIASES_CACHE is None:
        try:
            alias_data = json.loads(CATEGORY_ALIAS_FILE.read_text(encoding="utf-8"))
        except Exception:
            alias_data = {}
        alias_to_canon = {}
        for canon, names in alias_data.items():
            if canon != "power_actions" and isinstance(names, list):
                for a in names:
                    alias_to_canon[str(a).lower()] = canon
        _CATEGORY_ALIASES_CACHE = {
            "map": alias_to_canon,
            "power_actions": set(alias_data.get("power_actions") or []),
        }
    return _CATEGORY_ALIASES_CACHE


def control_windows_system(
    category: str,
    enabled: bool | None = None,
    mode: str | None = None,
    level: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatches structured system control commands to WindowsSystemController."""
    cat = (category or "").lower().strip()
    aliases = _get_category_aliases()
    canon = aliases["map"].get(cat, cat)
    if canon == "wifi":
        if enabled is not None:
            return WindowsSystemController.set_radio_state("wifi", bool(enabled))
        return WindowsSystemController.get_wifi_status()
    if canon == "bluetooth":
        if enabled is not None:
            return WindowsSystemController.set_radio_state("bluetooth", bool(enabled))
        return WindowsSystemController.get_radio_state("bluetooth")
    if canon == "theme":
        target_mode = mode or ("dark" if enabled is False else "light" if enabled is True else "dark")
        return WindowsSystemController.set_color_theme(target_mode)
    if canon == "audio":
        return WindowsSystemController.set_audio(level=level, mute_state=(not enabled if enabled is not None else None))
    if canon == "brightness":
        if level is not None:
            return WindowsSystemController.set_brightness(level)
        return WindowsSystemController.get_brightness()
    if canon == "settings":
        return WindowsSystemController.open_settings_page(mode or kwargs.get("uri", ""))
    if canon == "power":
        power_actions = aliases["power_actions"]
        return WindowsSystemController.system_power(cat if cat in power_actions else (mode or "sleep"))
    return {"status": "error", "message": f"Unknown category: {category}"}


# --------------------------------------------------------------------------- #
# Screen Pen Integration Forwarders
# --------------------------------------------------------------------------- #
try:
    from amigo.services.screen_pen import (
        ScreenPenOverlay,
        screen_pen_annotate,
        PEN_TOOL_DEFINITION,
    )
except Exception as _pen_err:
    log.warning("Screen pen overlay unavailable: %s", _pen_err)
    ScreenPenOverlay = None  # type: ignore

    def screen_pen_annotate(*args, **kwargs):
        return {"status": "error", "code": "PEN_UNAVAILABLE"}

    PEN_TOOL_DEFINITION = {}  # type: ignore