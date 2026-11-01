"""
OS automation layer for the Amigo voice assistant (Windows-first).

Design notes
------------
* Nothing environment-specific is hard-wired: tunables live in the CONFIG block
  and can be overridden with AMIGO_* environment variables; app names are
  resolved dynamically against running processes / window titles, with an
  optional user-editable alias file for spoken names ("vs code" -> "code").
* Every public function keeps its original name and return type
  (bool for actions, (bool, str) for set_volume / set_brightness,
  dict for get_system_status, dict | None for get_system_media_info).
* Failures are logged and reported through the return value, never raised.
"""
from __future__ import annotations

import asyncio
import ctypes
import difflib
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

try:  # pyautogui import can fail on headless / locked-down systems
    import pyautogui
except Exception as _gui_err:  # pragma: no cover
    pyautogui = None
    _GUI_IMPORT_ERROR = _gui_err
else:
    _GUI_IMPORT_ERROR = None

# --------------------------------------------------------------------------- #
# Logging (keeps the old "[OS Automation]" console look, but is controllable)
# --------------------------------------------------------------------------- #
log = logging.getLogger("os_automation")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[OS Automation] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #


def _env_number(name: str, default, cast=float):
    try:
        return cast(os.environ[name])
    except (KeyError, ValueError):
        return default


FOCUS_SETTLE_DELAY = _env_number("AMIGO_FOCUS_DELAY", 0.1)          # before typing
PASTE_SETTLE_DELAY = _env_number("AMIGO_PASTE_DELAY", 0.15)         # before clipboard restore
NEW_TAB_DELAY = _env_number("AMIGO_NEW_TAB_DELAY", 0.3)             # tab -> address bar ready
TYPE_INTERVAL = _env_number("AMIGO_TYPE_INTERVAL", 0.015)
DEFAULT_SCROLL = _env_number("AMIGO_SCROLL_AMOUNT", 600, int)
DEFAULT_VOLUME_STEP = _env_number("AMIGO_VOLUME_STEP", 10, int)     # percent
DEFAULT_RESTART_DELAY = _env_number("AMIGO_RESTART_DELAY", 30, int) # seconds
CLOSE_GRACE_SECONDS = _env_number("AMIGO_CLOSE_GRACE", 2.0)
FUZZY_CUTOFF = _env_number("AMIGO_FUZZY_CUTOFF", 0.8)
MEDIA_QUERY_TIMEOUT = _env_number("AMIGO_MEDIA_TIMEOUT", 8.0)
SUBPROCESS_TIMEOUT = _env_number("AMIGO_SUBPROCESS_TIMEOUT", 10.0)
# Mouse-in-corner emergency stop. Off by default because a parked cursor would
# otherwise make *every* automation call raise FailSafeException.
PYAUTOGUI_FAILSAFE = os.environ.get("AMIGO_PYAUTOGUI_FAILSAFE", "0") == "1"

MAX_SHUTDOWN_DELAY = 315_360_000  # Windows' documented /t ceiling

_BASE_DIR = Path(__file__).resolve().parent
MEDIA_SCRIPT = Path(os.environ.get("AMIGO_MEDIA_SCRIPT", _BASE_DIR / "get_media.ps1"))
ALIAS_FILE = Path(os.environ.get("AMIGO_APP_ALIASES", _BASE_DIR / "app_aliases.json"))

# Words that mean "the thing in front of me" rather than a named app.
_GENERIC_TARGET_WORDS = frozenset({
    "current", "active", "this", "that", "the", "window", "app", "application",
    "program", "it", "foreground", "focused",
})
_FILLER_AFFIX = re.compile(r"^(?:the|my)\s+|\s+(?:app|application|program|window)$")

# Never terminate these; the OS (or the user's session) depends on them.
_PROTECTED_PROCESSES = frozenset({
    "system", "system idle process", "registry", "smss", "csrss", "wininit",
    "winlogon", "services", "lsass", "svchost", "dwm", "fontdrvhost", "sihost",
    "taskhostw", "ctfmon", "runtimebroker", "searchhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost", "securityhealthservice", "msmpeng",
})
# Windows of these may be closed, but the process itself must never be killed.
_WINDOW_ONLY_PROCESSES = frozenset({"explorer", "applicationframehost"})
# Desktop / taskbar windows must never receive WM_CLOSE (Alt+F4 on the desktop
# opens the shutdown dialog).
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
    "minimize_all": ("win", "d"),   # Win+D toggles show-desktop
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
E_UNEXPECTED = 0x8000FFFF  # SHEmptyRecycleBin result when the bin is already empty
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
    """Accepts 50, '50', '50%', 'set it to 50 percent', 49.7 ... -> clamped 0-100."""
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


# --- ctypes bindings (private DLL instances so we don't change argtypes for
#     other modules that share ctypes.windll) --------------------------------
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
        u._enum_proc_type = enum_proc  # keep reference
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
    """The assistant itself plus the chain that launched it (until the shell)."""
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
# Typing
# --------------------------------------------------------------------------- #
def _send_unicode(text: str) -> None:
    """Types any Unicode text (Hindi, emoji, accents...) via SendInput.
    pyautogui.write() silently drops every non-ASCII character."""
    u = _user32()
    gui = _gui()
    for segment in re.split(r"(\n|\t)", text.replace("\r\n", "\n")):
        if segment == "\n":
            gui.press("enter")
            continue
        if segment == "\t":
            gui.press("tab")
            continue
        data = segment.encode("utf-16-le")  # surrogate pairs handled per unit
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
                    "SendInput was blocked (the target window may be running as administrator)"
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
    """Paste via clipboard, restoring the user's previous text clipboard afterwards."""
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
        time.sleep(PASTE_SETTLE_DELAY)  # let the target app read the clipboard first
        if previous:  # only restore real text; never wipe an image/file clipboard
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
# Scrolling / browser tabs
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
        gui.hotkey("ctrl", "l")  # make sure the address bar owns focus
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
# Shortcuts & window management
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
    """Presses a combination of keys (e.g. 'ctrl+c', 'control plus shift + t', 'alt tab')."""
    gui = _gui()
    key_list = _normalize_keys(keys)
    if not key_list:
        log.warning("Empty shortcut: %r", keys)
        return False
    valid = set(gui.KEYBOARD_KEYS)
    unknown = [k for k in key_list if k not in valid]
    if unknown:
        # pyautogui silently ignores unknown keys, which would fake a success.
        log.warning("Unknown key(s) %s in shortcut %r", unknown, keys)
        return False
    time.sleep(FOCUS_SETTLE_DELAY)
    try:
        gui.hotkey(*key_list)
    except Exception:
        for k in reversed(key_list):  # don't leave modifiers stuck down
            try:
                gui.keyUp(k)
            except Exception:
                pass
        raise
    log.info("Pressed shortcut: %s", "+".join(key_list))
    return True


def _close_foreground_window() -> bool:
    """Gracefully closes the focused window, refusing desktop/taskbar/the assistant."""
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
    if pid in _protected_pids() or (title and "amigo" in title.lower()):
        log.warning("Foreground window belongs to the assistant itself; not closing it")
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
# Closing applications
# --------------------------------------------------------------------------- #
def _squash(s: str) -> str:
    return re.sub(r"[\s_.\-]+", "", s.lower())


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


def _normalize_app_name(app_name) -> str:
    raw = (app_name or "").strip().rstrip(".!?,;:")
    clean = re.sub(r"[^\w\s-]", "", raw.lower()).strip()
    clean = _FILLER_AFFIX.sub("", clean).strip()
    return clean


def _is_generic_target(clean: str) -> bool:
    tokens = clean.split()
    return not tokens or all(t in _GENERIC_TARGET_WORDS for t in tokens)


def _close_process_group(procs, windows, *, allow_terminate: bool, force: bool) -> bool:
    """Ask politely (WM_CLOSE), wait, and only then escalate.
    Never force-kills a process that still shows a window (e.g. an unsaved-changes
    prompt) unless force=True."""
    import psutil

    pids = {p.pid for p in procs}
    hwnds = [h for h, pid, _t, _c in windows if pid in pids]
    if hwnds:
        _post_close(hwnds)
        _wait_windows_gone(hwnds, CLOSE_GRACE_SECONDS)

    def _stem(p):
        try:
            return re.sub(r"\.exe$", "", p.name().lower())
        except Exception:  # process already gone
            return ""

    names = {p.pid: _stem(p) for p in procs}
    killable = [p for p in procs
                if p.is_running() and names.get(p.pid) not in _WINDOW_ONLY_PROCESSES]
    # window-only processes (explorer...) count as closed once their windows are gone
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
                log.info("Left %d process(es) running: they still show a window "
                         "(possibly an unsaved-changes prompt)", len(held_open))
        elif alive:
            log.info("%d process(es) did not exit after a close request", len(alive))
    return closed_any


@_guarded("closing app")
def close_app(app_name: str = "", force: bool = False) -> bool:
    """Closes an application by name, or the active window if no target was named.

    Matching is dynamic: exact process name, then fuzzy process name (handles
    speech-recognition slips), then window title. Graceful close first; processes
    are terminated only if they have no window left (tray apps) or force=True.
    """
    clean = _normalize_app_name(app_name)
    if clean in ("amigo", "amigo assistant", "amigo voice assistant"):
        log.warning("Refusing to close the assistant itself via close_app")
        return False
    if _is_generic_target(clean):
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

    # Tier 1: exact process name ("chrome", "vs code" ~ "vscode")
    target = _squash(clean)
    exact = [s for s in procs_by_stem if _squash(s) == target]
    if exact:
        procs = [p for s in exact for p in procs_by_stem[s]]
        if _close_process_group(procs, windows, allow_terminate=True, force=force):
            log.info("Closed '%s' (process match)", clean)
            return True
        return False

    # Tier 2: fuzzy process name, only among processes that own a visible window,
    # graceful close only (a wrong guess must not force-kill anything).
    windowed = {pid for _h, pid, _t, _c in windows}
    windowed_stems = {_squash(s): s for s, ps in procs_by_stem.items()
                      if any(p.pid in windowed for p in ps)}
    close = difflib.get_close_matches(target, list(windowed_stems), n=1, cutoff=FUZZY_CUTOFF)
    if close:
        stem = windowed_stems[close[0]]
        if _close_process_group(procs_by_stem[stem], windows, allow_terminate=False, force=False):
            log.info("Closed '%s' (fuzzy match: %s)", clean, stem)
            return True
        return False

    # Tier 3: window titles containing every word of the request.
    tokens = clean.split()
    title_hits = [
        (h, pid, t) for h, pid, t, _c in windows
        if pid not in protected and all(re.search(rf"\b{re.escape(tok)}\b", t.lower()) for tok in tokens)
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
    """Puts the computer to sleep (suspend). SetSuspendState blocks until resume,
    so it runs on a worker thread and the assistant stays responsive.

    Note: on machines where hibernation is enabled, Windows may hibernate
    instead of sleeping. Disable it with `powercfg /h off` if that matters."""
    _require_windows()
    powrprof = ctypes.WinDLL("powrprof")
    fn = powrprof.SetSuspendState
    fn.argtypes = [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
    fn.restype = wintypes.BOOLEAN
    result: dict = {}

    def _run():
        result["ok"] = bool(fn(False, False, False))  # (hibernate, force[ignored], wake-events)

    t = threading.Thread(target=_run, name="amigo-sleep", daemon=True)
    t.start()
    t.join(1.5)
    if not t.is_alive() and not result.get("ok"):
        raise OSError("SetSuspendState failed (sleep unsupported or blocked by policy)")
    log.info("System sleeping")
    return True


@_guarded("emptying recycle bin")
def empty_recycle_bin():
    """Empties the Windows Recycle Bin silently. (Irreversible: confirm upstream.)"""
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
    """Schedules a system restart (default delay gives time to cancel_shutdown())."""
    _require_windows()
    seconds = _to_int(delay, DEFAULT_RESTART_DELAY) if delay is not None else DEFAULT_RESTART_DELAY
    seconds = max(0, min(MAX_SHUTDOWN_DELAY, seconds))
    exe = _system_exe("shutdown")
    r = _run_hidden([exe, "/r", "/t", str(seconds)])
    if r.returncode == ERROR_SHUTDOWN_IN_PROGRESS:  # one is already pending: replace it
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
        session = mgr.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        status = session.get_playback_info().playback_status
        status = getattr(status, "name", str(status)).lower()
        return {"status": status, "title": props.title or "", "artist": props.artist or ""}

    # Own thread + loop: asyncio.run() raises if the assistant already has a running loop.
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


def get_system_media_info() -> dict | None:
    """Real-time media title, artist and playback status from Windows' media
    transport controls. Prefers the in-process API; falls back to get_media.ps1."""
    if not _IS_WINDOWS:
        return None
    for source in (_media_info_via_winrt, _media_info_via_script):
        try:
            info = source()
        except Exception as exc:
            log.warning("Media query via %s failed: %s", source.__name__, exc)
            continue
        if info is _UNAVAILABLE:
            continue
        return info if info and info.get("title") else None
    log.info("No media backend available (install `winsdk` or provide %s)", MEDIA_SCRIPT.name)
    return None


# --------------------------------------------------------------------------- #
# Volume
# --------------------------------------------------------------------------- #
def _with_endpoint_volume(fn):
    """Runs fn(IAudioEndpointVolume) with correct COM init/teardown."""
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        if hasattr(device, "EndpointVolume"):  # newer pycaw
            endpoint = device.EndpointVolume
        else:                                   # older pycaw
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = cast(iface, POINTER(IAudioEndpointVolume))
        try:
            return fn(endpoint)
        finally:
            del endpoint  # release before CoUninitialize
    finally:
        comtypes.CoUninitialize()


def get_volume() -> int | None:
    """Current master volume 0-100, or None if it can't be read."""
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
    # Fallback: each media-key press is ~2 percent on Windows
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
    """Sets master volume to a percentage (0-100) via the Windows Core Audio API.
    Also unmutes when raising the volume above 0."""
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


# --------------------------------------------------------------------------- #
# Brightness / system status
# --------------------------------------------------------------------------- #
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
        return {  # None (not 0) so callers can't mistake a failure for real readings
            "cpu": None, "ram": None, "battery": None, "plugged": None,
            "summary": "Could not read system hardware status.",
        }
