"""
os_settings.py - change (not just open) Windows settings from plain language.

Nothing about individual settings is hard-coded in this file
-------------------------------------------------------------
* Where a setting lives, which value means what, and the words people use for
  it are DATA in settings_catalog.json (plus an optional settings_user.json you
  can edit freely). Add a setting = add a JSON entry, no code change.
* Some things are discovered from the live machine instead of listed anywhere:
    - every power setting      -> parsed from `powercfg /qh`
    - time zones, power plans  -> enumerated by the command named in the catalog
    - Wi-Fi / Bluetooth / ...  -> enumerated through the WinRT Radio API
* Anything still unknown is reached through the Settings app itself: its own
  search box is used to find the page, then the matching toggle / slider is
  operated through UI Automation (needs `pip install uiautomation`).

Public API (all return (ok, spoken_message) unless noted; never raise)
-----------------------------------------------------------------------
    change_setting(setting, value=None, confirmed=False)
    get_setting(setting)
    toggle_setting(setting)
    list_settings(query=None) -> list[dict]
    reload_catalog()
    validate_catalog() -> list[str]   # problems found in the JSON, [] if clean

Examples
--------
    change_setting("switch to light mode")
    change_setting("night light", "on")
    change_setting("sleep after 30 minutes on battery")
    change_setting("mouse speed", "increase")
    change_setting("time zone", "India")
    change_setting("firewall", "off")            -> asks for confirmation first
    change_setting("firewall", "off", confirmed=True)
"""
from __future__ import annotations

import asyncio
import ctypes
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("os_automation")  # same logger/handler as os_automation.py


# --------------------------------------------------------------------------- #
# CONFIG (every value can be overridden with an AMIGO_* environment variable)
# --------------------------------------------------------------------------- #
def _env(name: str, default, cast=float):
    try:
        return cast(os.environ[name])
    except (KeyError, ValueError):
        return default


_BASE = Path(__file__).resolve().parent
CATALOG_FILE = Path(os.environ.get("AMIGO_SETTINGS_CATALOG", _BASE / "settings_catalog.json"))
USER_CATALOG_FILE = Path(os.environ.get("AMIGO_SETTINGS_USER_CATALOG", _BASE / "settings_user.json"))
AUTOMATION_MODULE = os.environ.get("AMIGO_AUTOMATION_MODULE", "os_automation")

MATCH_CUTOFF = _env("AMIGO_SETTINGS_CUTOFF", 0.6)          # below this: "no such setting"
OPTION_COVER = _env("AMIGO_SETTINGS_OPTION_COVER", 0.8)    # share of your words an option must explain
CONFIDENT_SCORE = _env("AMIGO_SETTINGS_CONFIDENT", 0.85)   # below this: also try discovery
AMBIGUITY_MARGIN = _env("AMIGO_SETTINGS_MARGIN", 0.06)
DEFAULT_TIME_UNIT_SECONDS = _env("AMIGO_SETTINGS_DEFAULT_UNIT", 60, int)  # "sleep after 30" = minutes
CMD_TIMEOUT = _env("AMIGO_SETTINGS_CMD_TIMEOUT", 15.0)
RADIO_TIMEOUT = _env("AMIGO_SETTINGS_RADIO_TIMEOUT", 10.0)
CACHE_SECONDS = _env("AMIGO_SETTINGS_CACHE", 30.0)
VERIFY_DELAY = _env("AMIGO_SETTINGS_VERIFY_DELAY", 0.2)
UI_FALLBACK = os.environ.get("AMIGO_SETTINGS_UI_FALLBACK", "1") != "0"
UI_OPEN_DELAY = _env("AMIGO_SETTINGS_UI_OPEN_DELAY", 1.5)
UI_STEP_DELAY = _env("AMIGO_SETTINGS_UI_STEP_DELAY", 1.0)
UI_TIMEOUT = _env("AMIGO_SETTINGS_UI_TIMEOUT", 6.0)
UI_MAX_DEPTH = _env("AMIGO_SETTINGS_UI_DEPTH", 25, int)

_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HIVES = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE",
          "HKCR": "HKEY_CLASSES_ROOT", "HKU": "HKEY_USERS"}
_REG_TYPES = {"dword": "REG_DWORD", "qword": "REG_QWORD", "sz": "REG_SZ", "expand_sz": "REG_EXPAND_SZ"}
_VALID_KINDS = {"toggle", "choice", "number", "text", "action"}
_VALID_TARGETS = {"registry", "spi", "command", "radio", "callable"}
_SPIF_UPDATE_AND_BROADCAST = 0x03  # SPIF_UPDATEINIFILE | SPIF_SENDCHANGE


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _fmt_num(n) -> str:
    if n is None:
        return ""
    f = float(n)
    return str(int(f)) if f.is_integer() else f"{f:.1f}"


def _human_seconds(sec: float) -> str:
    sec = int(round(sec))
    if sec == 0:
        return "never"
    if sec % 3600 == 0:
        h = sec // 3600
        return f"{h} hour{'s' if h != 1 else ''}"
    if sec % 60 == 0:
        m = sec // 60
        return f"{m} minute{'s' if m != 1 else ''}"
    return f"{sec} seconds"


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _winreg():
    import winreg  # Windows only; ImportError elsewhere is reported as a failure
    return winreg


def _system_exe(name: str) -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if root:
        candidate = Path(root) / "System32" / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which(name) or name


def _resolve_exe(name: str) -> str:
    if os.path.dirname(name):
        return name
    return _system_exe(re.sub(r"\.exe$", "", name, flags=re.I))


def _powershell() -> str:
    return shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def _run(argv: list[str], env_extra: dict | None = None, timeout: float | None = None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout or CMD_TIMEOUT, creationflags=_NO_WINDOW, env=env,
    )


def _run_checked(argv: list[str], env_extra: dict | None = None, ok_codes=(0,)):
    r = _run(argv, env_extra)
    if r.returncode not in ok_codes:
        raise OSError(f"{Path(argv[0]).name} exited {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}")
    return r


# --------------------------------------------------------------------------- #
# Catalog + language (all data comes from JSON)
# --------------------------------------------------------------------------- #
def _tokens(text) -> list[str]:
    return re.findall(r"[^\W_]+", str(text).lower())


class _Lang:
    """Vocabulary from the catalog's "language" block (edit it to add other languages)."""

    def __init__(self, data: dict):
        def words(key):
            return frozenset(w.lower() for w in data.get(key, [])
                             if isinstance(w, str) and len(_tokens(w)) == 1)

        self.on, self.off, self.toggle = words("on"), words("off"), words("toggle")
        self.up, self.down, self.never = words("up"), words("down"), words("never")
        self.relative, self.fillers = words("relative"), words("fillers")
        src = data.get("power_source", {})
        self.ac = frozenset(w.lower() for w in src.get("ac", []))
        self.dc = frozenset(w.lower() for w in src.get("dc", []))
        self.time_units = {k.lower(): int(v) for k, v in data.get("time_units", {}).items()}
        self.modifiers = self.up | self.down | self.never
        self.stop = (self.fillers | self.on | self.off | self.toggle | self.relative
                     | self.ac | self.dc)


_state: dict[str, Any] = {"lang": None, "settings": None, "discovery": {}, "power": (0.0, [])}


def _merge_language(dst: dict, src: dict) -> None:
    for key, val in src.items():
        if isinstance(val, list):
            dst.setdefault(key, [])
            dst[key] = list(dict.fromkeys(dst[key] + val))
        elif isinstance(val, dict):
            dst.setdefault(key, {})
            for k2, v2 in val.items():
                if isinstance(v2, list):
                    dst[key][k2] = list(dict.fromkeys(dst[key].get(k2, []) + v2))
                else:
                    dst[key][k2] = v2


def _read_catalog_files() -> tuple[dict, list[dict], dict]:
    language: dict = {}
    specs: dict[str, dict] = {}
    discovery: dict = {}
    for path in (CATALOG_FILE, USER_CATALOG_FILE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if path == CATALOG_FILE:
                log.warning("Settings catalog not found: %s", path)
            continue
        except Exception as exc:
            log.warning("Could not read settings catalog %s: %s", path, exc)
            continue
        _merge_language(language, data.get("language", {}))
        for spec in data.get("settings", []):
            if isinstance(spec, dict) and spec.get("id"):
                specs[spec["id"]] = spec  # later files override earlier ones
        discovery.update(data.get("discovery", {}))
    return language, list(specs.values()), discovery


def _lang() -> _Lang:
    if _state["lang"] is None:
        _load()
    return _state["lang"]


def _load() -> None:
    language, specs, discovery = _read_catalog_files()
    _state["lang"] = _Lang(language)
    _state["discovery"] = discovery
    _state["settings"] = []
    for spec in specs:
        try:
            _state["settings"].append(Setting(spec))
        except Exception as exc:
            log.warning("Skipping invalid catalog entry %r: %s", spec.get("id"), exc)
    _state["power"] = (0.0, [])


def reload_catalog() -> int:
    """Re-reads the JSON catalog files; returns the number of settings loaded."""
    _state["lang"] = None
    _state["settings"] = None
    _load()
    return len(_state["settings"])


def _catalog_settings() -> list["Setting"]:
    if _state["settings"] is None:
        _load()
    return _state["settings"]


# --------------------------------------------------------------------------- #
# Request parsing + fuzzy matching
# --------------------------------------------------------------------------- #
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Request:
    raw: str
    subject: list            # content words, no numbers / up-down-never
    subject_n: list          # same, numbers kept ("24 hour clock")
    content: list            # every non-filler word, numbers/units/up/down/never kept (option matching)
    polarity: str | None = None     # on / off / toggle
    direction: str | None = None    # up / down
    relative: bool = False
    never: bool = False
    number: float | None = None
    unit_seconds: int | None = None
    source: str | None = None       # ac / dc
    value: str = ""


@dataclass
class Match:
    score: float
    setting: "Setting"
    option: str | None


def _parse(setting, value=None) -> Request:
    lang = _lang()
    s = "" if setting is None else str(setting)
    v = "" if value is None else str(value)
    toks_s, toks_v = _tokens(s), _tokens(v)
    toks = toks_s + toks_v

    polarity = None
    for group in (toks_v, toks_s):  # an explicit value beats words inside the setting text
        for i, t in enumerate(group):
            nxt = group[i + 1] if i + 1 < len(group) else ""
            if nxt in lang.ac or nxt in lang.dc:
                continue  # "on battery" names a power source; it isn't "turn on"
            p = ("toggle" if t in lang.toggle else "on" if t in lang.on
                 else "off" if t in lang.off else None)
            if p:
                polarity = p
        if polarity:
            break

    direction = None
    for t in toks:
        if t in lang.up:
            direction = "up"
        elif t in lang.down:
            direction = "down"

    m = _NUM.search(v) or _NUM.search(s)
    number = float(m.group()) if m else None
    unit_idx = {i for i, t in enumerate(toks) if t in lang.time_units and i > 0 and toks[i - 1].isdigit()}
    unit_seconds = next((lang.time_units[toks[i]] for i in sorted(unit_idx)), None)  # "30 minutes"
    source = next(("ac" if t in lang.ac else "dc" for t in toks if t in lang.ac or t in lang.dc), None)

    content = [t for t in toks if t not in lang.stop]                       # for option matching
    subject = [t for i, t in enumerate(toks)
               if t not in lang.stop and t not in lang.modifiers and not t.isdigit() and i not in unit_idx]
    subject_n = [t for t in toks if t not in lang.stop and t not in lang.modifiers]
    if not subject:
        subject = [t for t in toks if not t.isdigit()]
    if not subject_n:
        subject_n = list(toks)
    return Request(
        raw=f"{s} {v}".strip(), subject=subject, subject_n=subject_n, content=content,
        polarity=polarity, direction=direction,
        relative=any(t in lang.relative for t in toks),
        never=any(t in lang.never for t in toks),
        number=number, unit_seconds=unit_seconds, source=source, value=v.strip(),
    )


def _phrase_tokens(items) -> list[list[str]]:
    lang = _lang()
    out = []
    for item in items:
        toks = _tokens(item)
        out.append([t for t in toks if t not in lang.stop] or toks)
    return [p for p in out if p]


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a.isdigit() or b.isdigit():
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return r if r >= 0.8 else 0.0


def _fscore(q: list[str], p: list[str]) -> float:
    """Fuzzy token-set F1 between a query and a phrase (1.0 = same words)."""
    if not q or not p:
        return 0.0
    used: set[int] = set()
    total = 0.0
    for pt in p:
        best, bi = 0.0, None
        for i, qt in enumerate(q):
            if i in used:
                continue
            s = _sim(pt, qt)
            if s > best:
                best, bi = s, i
        if bi is not None:
            used.add(bi)
            total += best
    rec, prec = total / len(p), total / len(q)
    return 0.0 if rec + prec == 0 else 2 * rec * prec / (rec + prec)


def _cover(q: list[str], p: list[str]) -> float:
    """Fraction of the query words that the phrase explains (query may be a subset of p)."""
    if not q or not p:
        return 0.0
    used: set[int] = set()
    total = 0.0
    for qt in q:
        best, bi = 0.0, None
        for i, pt in enumerate(p):
            if i in used:
                continue
            s = _sim(qt, pt)
            if s > best:
                best, bi = s, i
        if bi is not None:
            used.add(bi)
            total += best
    return total / len(q)


def _rank(req: Request, settings: list["Setting"]) -> list[Match]:
    out = []
    for st in settings:
        score, opt = st.score(req)
        out.append(Match(score, st, opt))
    out.sort(key=lambda m: (-m.score, m.setting.priority))
    return out


# --------------------------------------------------------------------------- #
# Backends: registry
# --------------------------------------------------------------------------- #
def _reg_hive(wr, t):
    return getattr(wr, _HIVES[t["hive"].upper()])


def _reg_read(t: dict):
    wr = _winreg()
    try:
        with wr.OpenKey(_reg_hive(wr, t), t["path"], 0, wr.KEY_READ | wr.KEY_WOW64_64KEY) as key:
            return wr.QueryValueEx(key, t["name"])[0]
    except FileNotFoundError:
        return None


def _reg_write(t: dict, value) -> None:
    wr = _winreg()
    hive, wow = _reg_hive(wr, t), wr.KEY_WOW64_64KEY
    if value is None:  # "off" is represented by the value not existing
        try:
            with wr.OpenKey(hive, t["path"], 0, wr.KEY_SET_VALUE | wow) as key:
                wr.DeleteValue(key, t["name"])
        except FileNotFoundError:
            pass
        return
    typ = getattr(wr, _REG_TYPES[t.get("dtype", "dword")])
    value = int(value) if typ in (wr.REG_DWORD, wr.REG_QWORD) else str(value)
    with wr.CreateKeyEx(hive, t["path"], 0, wr.KEY_SET_VALUE | wow) as key:
        wr.SetValueEx(key, t["name"], 0, typ, value)


# --------------------------------------------------------------------------- #
# Backends: SystemParametersInfo (mouse, keyboard, wallpaper, screen saver...)
# --------------------------------------------------------------------------- #
_user32_cache = None


def _user32():
    global _user32_cache
    if _user32_cache is None:
        if not _IS_WINDOWS:
            raise OSError("this setting is only available on Windows")
        u = ctypes.WinDLL("user32", use_last_error=True)  # private instance: our argtypes stay ours
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        u.SystemParametersInfoW.restype = wintypes.BOOL
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                          wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                                          ctypes.POINTER(ctypes.c_size_t)]
        u.SendMessageTimeoutW.restype = ctypes.c_size_t
        _user32_cache = u
    return _user32_cache


def _spi_get(t: dict):
    u = _user32()
    if "get_metric" in t:  # a few settings have no SPI getter but a system metric
        return u.GetSystemMetrics(int(t["get_metric"]))
    mode, act = t.get("mode", "uiparam"), int(t["get"])
    if mode == "intarray":
        buf = (ctypes.c_int * int(t.get("length", 3)))()
        ok = u.SystemParametersInfoW(act, 0, buf, 0)
        result = list(buf)
    elif mode == "string":
        buf = ctypes.create_unicode_buffer(520)
        ok = u.SystemParametersInfoW(act, 520, buf, 0)
        result = buf.value
    else:
        val = ctypes.c_int()
        ok = u.SystemParametersInfoW(act, 0, ctypes.byref(val), 0)
        result = val.value
    if not ok:
        raise OSError(f"SystemParametersInfo get {act} failed (error {ctypes.get_last_error()})")
    return result


def _spi_set(t: dict, value) -> None:
    u = _user32()
    mode, act = t.get("mode", "uiparam"), int(t["set"])
    if mode == "intarray":
        arr = (ctypes.c_int * len(value))(*[int(x) for x in value])
        ok = u.SystemParametersInfoW(act, 0, arr, _SPIF_UPDATE_AND_BROADCAST)
    elif mode == "string":
        buf = ctypes.create_unicode_buffer(str(value))
        ok = u.SystemParametersInfoW(act, 0, buf, _SPIF_UPDATE_AND_BROADCAST)
    elif mode == "pvvalue":
        ok = u.SystemParametersInfoW(act, 0, int(value), _SPIF_UPDATE_AND_BROADCAST)
    else:
        ok = u.SystemParametersInfoW(act, int(value), None, _SPIF_UPDATE_AND_BROADCAST)
    if not ok:
        raise OSError(f"SystemParametersInfo set {act} failed (error {ctypes.get_last_error()})")


# --------------------------------------------------------------------------- #
# Backends: commands / PowerShell
# --------------------------------------------------------------------------- #
def _sub(arg: str, ctx: dict) -> str:
    return re.sub(r"\{(value|number|text|state)\}", lambda m: str(ctx.get(m.group(1)) or ""), arg)


def _cmd_env(ctx: dict) -> dict:
    # Values reach PowerShell through the environment, never spliced into the script text.
    return {"AMIGO_VALUE": str(ctx.get("value") if ctx.get("value") is not None else ""),
            "AMIGO_NUMBER": _fmt_num(ctx.get("number")), "AMIGO_TEXT": str(ctx.get("text") or ""),
            "AMIGO_STATE": str(ctx.get("state") or "")}


def _ps_argv(script: str) -> list[str]:
    return [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", "$ErrorActionPreference='Stop'; " + script]


def _cmd_write(t: dict, ctx: dict) -> None:
    if t.get("set_ps"):
        argv = _ps_argv(t["set_ps"])
    else:
        spec = t["set"]
        if isinstance(spec, dict):
            if ctx.get("state") not in spec:
                return  # this target doesn't act on that state
            spec = spec[ctx["state"]]
        argv = [_sub(a, ctx) for a in spec]
        argv[0] = _resolve_exe(argv[0])
    _run_checked(argv, _cmd_env(ctx), tuple(t.get("ok_codes", [0])))


def _cmd_read(t: dict):
    if t.get("get_ps"):
        argv = _ps_argv(t["get_ps"])
    elif t.get("get"):
        argv = list(t["get"])
        argv[0] = _resolve_exe(argv[0])
    else:
        return None
    r = _run(argv)
    if r.returncode != 0:
        return None
    out = r.stdout
    if t.get("regex"):
        m = re.search(t["regex"], out, re.M)
        if not m:
            return None
        return m.group("value") if "value" in m.groupdict() else m.group(1)
    return out.strip() or None


# --------------------------------------------------------------------------- #
# Backends: radios (Wi-Fi, Bluetooth, airplane mode) via WinRT
# --------------------------------------------------------------------------- #
def _winrt_radio_api():
    try:
        from winsdk.windows.devices.radios import Radio, RadioAccessStatus, RadioState
    except ImportError:
        from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioState
    return Radio, RadioAccessStatus, RadioState


def _radio_op(kind: str, new_state: str | None):
    """new_state None -> read ('on'/'off'/None for mixed); else set it."""
    Radio, Access, RadioState = _winrt_radio_api()
    want = kind.lower().replace("_", "")

    async def go():
        if await Radio.request_access_async() != Access.ALLOWED:
            raise PermissionError("Windows blocked radio access (Settings > Privacy & security)")
        radios = [r for r in await Radio.get_radios_async()
                  if want == "all" or r.kind.name.lower().replace("_", "") == want]
        if not radios:
            raise OSError(f"no {kind} radio found on this device")
        if new_state is None:
            states = {r.state.name.lower() for r in radios}
            return next(iter(states)) if len(states) == 1 and states <= {"on", "off"} else None
        target = RadioState.ON if new_state == "on" else RadioState.OFF
        for r in radios:
            if await r.set_state_async(target) != Access.ALLOWED:
                raise PermissionError(f"Windows refused to switch the {kind} radio")
        return new_state

    pool = ThreadPoolExecutor(max_workers=1)  # own loop: the assistant may already run one
    try:
        return pool.submit(lambda: asyncio.run(go())).result(timeout=RADIO_TIMEOUT)
    finally:
        pool.shutdown(wait=False)


# --------------------------------------------------------------------------- #
# Backends: call a function from the assistant's own automation module
# --------------------------------------------------------------------------- #
def _callable(name: str):
    import importlib
    try:
        mod = importlib.import_module(AUTOMATION_MODULE)
    except ModuleNotFoundError:
        mod = importlib.import_module(f"amigo.services.{AUTOMATION_MODULE}")
    return getattr(mod, name)


def _call_write(t: dict, ctx: dict) -> None:
    res = _callable(t["set"])(ctx.get("value"))
    ok = res[0] if isinstance(res, tuple) else res
    if ok is False:
        raise OSError(res[1] if isinstance(res, tuple) and len(res) > 1 else f"{t['set']} failed")


def _call_read(t: dict):
    return _callable(t["get"])() if t.get("get") else None


def _target_read(t: dict):
    kind = t["type"]
    if kind == "registry":
        return _reg_read(t)
    if kind == "spi":
        return _spi_get(t)
    if kind == "command":
        return _cmd_read(t)
    if kind == "radio":
        return _radio_op(t["radio"], None)
    if kind == "callable":
        return _call_read(t)
    raise ValueError(f"unknown target type {kind!r}")


def _target_write(t: dict, ctx: dict) -> None:
    kind = t["type"]
    if kind == "registry":
        _reg_write(t, ctx["value"])
    elif kind == "spi":
        _spi_set(t, ctx["value"])
    elif kind == "command":
        _cmd_write(t, ctx)
    elif kind == "radio":
        _radio_op(t["radio"], str(ctx["value"]))
    elif kind == "callable":
        _call_write(t, ctx)
    else:
        raise ValueError(f"unknown target type {kind!r}")


def _same(read, mapped, t: dict) -> bool:
    if mapped is None:
        return read is None
    if isinstance(mapped, list):
        if not isinstance(read, list):
            return False
        idx = t.get("match_index")
        return read[idx] == mapped[idx] if idx is not None else list(read) == mapped
    if isinstance(read, str) or isinstance(mapped, str):
        return str(read).strip().lower() == str(mapped).strip().lower()
    return read == mapped


# --------------------------------------------------------------------------- #
# After-change hooks (named in the catalog: "after": ["broadcast:ImmersiveColorSet"])
# --------------------------------------------------------------------------- #
def _broadcast(param: str) -> None:
    result = ctypes.c_size_t()
    _user32().SendMessageTimeoutW(0xFFFF, 0x001A, 0, param, 0x2, 3000, ctypes.byref(result))  # WM_SETTINGCHANGE


def _refresh_shell() -> None:
    ctypes.WinDLL("shell32").SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    _broadcast("ShellState")


def _restart_explorer() -> None:
    _run([_system_exe("taskkill"), "/f", "/im", "explorer.exe"])
    time.sleep(1.5)  # Windows normally relaunches the shell by itself
    import psutil
    if not any((p.info["name"] or "").lower() == "explorer.exe" for p in psutil.process_iter(["name"])):
        subprocess.Popen(["explorer.exe"], creationflags=_NO_WINDOW)


def _run_after(hooks: list[str]) -> None:
    for hook in hooks:
        kind, _, arg = hook.partition(":")
        try:
            if kind == "broadcast":
                _broadcast(arg)
            elif kind == "refresh_shell":
                _refresh_shell()
            elif kind == "restart_explorer":
                _restart_explorer()
            else:
                log.warning("Unknown after-hook %r", hook)
        except Exception as exc:
            log.warning("After-hook %r failed: %s", hook, exc)


# --------------------------------------------------------------------------- #
# Setting (catalog entry at run time) and PowerSetting (discovered)
# --------------------------------------------------------------------------- #
class Setting:
    priority = 0          # lower wins ties
    option_scoring = True # an option phrase ("dark mode") may select the whole setting

    def __init__(self, spec: dict):
        self.spec = spec
        self.id = spec["id"]
        self.name = spec.get("name") or self.id.replace("_", " ")
        self.kind = spec.get("kind", "toggle")
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")
        self.admin = bool(spec.get("admin"))
        self.confirm = bool(spec.get("confirm"))
        self.note = spec.get("note", "")
        self.after = list(spec.get("after", []))
        self.targets = list(spec.get("targets", []))
        self.read_target = spec.get("read_target")
        self.dynamic = spec.get("dynamic_options")
        self.unit = spec.get("unit", "")
        self.unit_kind = self.unit
        self.min, self.max = spec.get("min"), spec.get("max")
        self.step = spec.get("step", 10 if self.unit == "percent" else 1)
        self.tol = spec.get("tol", 1)
        self.option_aliases = {k: list(v) for k, v in spec.get("option_aliases", {}).items()}
        self._opts_cache: tuple[float, list] = (0.0, [])
        self.phrases = _phrase_tokens([self.name, self.id.replace("_", " "), *spec.get("aliases", [])])
        self.option_phrases: dict[str, list[list[str]]] = {}
        if not self.dynamic and self.kind in ("toggle", "choice"):
            for state, _label in self.options():
                items = list(self.option_aliases.get(state, []))
                if self.kind == "choice":
                    items.append(state)
                if items:
                    self.option_phrases[state] = _phrase_tokens(items)

    # ---- matching --------------------------------------------------------
    def score(self, req: Request) -> tuple[float, str | None]:
        best, opt = 0.0, None
        for q in (req.subject, req.subject_n):
            for p in self.phrases:
                best = max(best, _fscore(q, p))
        for state, plist in (self.option_phrases if self.option_scoring else {}).items():
            for q in (req.subject, req.subject_n):
                for p in plist:
                    sc = _fscore(q, p)
                    if sc >= best and sc > 0:
                        best, opt = sc, state
        return best, opt

    def _best_phrase(self, req: Request) -> list[str]:
        scored = [(max(_fscore(q, p) for q in (req.subject, req.subject_n)), p) for p in self.phrases]
        return max(scored, key=lambda x: x[0])[1] if scored else []

    # ---- options ---------------------------------------------------------
    def options(self) -> list[tuple[str, str]]:
        """[(state, label)]. Static: from the catalog. Dynamic: enumerated from the OS."""
        if self.dynamic:
            ts, cached = self._opts_cache
            if cached and time.monotonic() - ts < CACHE_SECONDS:
                return cached
            r = _run([_resolve_exe(self.dynamic["argv"][0]), *self.dynamic["argv"][1:]])
            found, seen = [], set()
            for m in re.finditer(self.dynamic["regex"], r.stdout):
                value, label = m.group("value").strip(), m.group("label").strip()
                if value.lower() not in seen:
                    seen.add(value.lower())
                    found.append((value, label))
            self._opts_cache = (time.monotonic(), found)
            return found
        if self.kind == "toggle":
            return [("on", "on"), ("off", "off")]
        keys: list[str] = []
        for t in self.targets:
            keys += [k for k in (t.get("values") or {}) if k not in keys]
        keys += [k for k in self.option_aliases if k not in keys]
        return [(k, k) for k in keys]

    def label_of(self, state) -> str:
        for value, label in self.options():
            if str(value).lower() == str(state).lower():
                return label
        return str(state)

    def _match_option(self, req: Request, options) -> tuple[str | None, str | None]:
        used = set(self._best_phrase(req))
        residual = [t for t in req.content if t not in used]
        if not residual:
            return None, None
        scored = []
        for value, label in options:
            phrases = self.option_phrases.get(value) or _phrase_tokens([label, value])
            if self.dynamic:
                phrases = _phrase_tokens([label, value])
            best = max(((_cover(residual, p), _fscore(residual, p)) for p in phrases), default=(0.0, 0.0))
            scored.append((best, value))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored or scored[0][0][0] < OPTION_COVER:
            return None, None
        if len(scored) > 1 and scored[1][0][0] >= OPTION_COVER and abs(scored[1][0][1] - scored[0][0][1]) < 1e-9:
            return None, f"more than one {self.name} matches that"
        return scored[0][1], None

    def can_resolve(self, req: Request, opt: str | None) -> bool:
        """Could this request be turned into a concrete change for this setting?"""
        try:
            if self.kind == "toggle":
                return bool(opt or req.polarity)
            if self.kind == "choice":
                if opt and not self.dynamic:
                    return True
                return self._match_option(req, self.options())[0] is not None
            if self.kind == "number":
                return req.number is not None or bool(req.direction) or req.never
            if self.kind == "text":
                return bool(req.value)
            return True
        except Exception:
            return False

    # ---- turning a request into a concrete target state ------------------
    def pick_state(self, req: Request, opt: str | None) -> tuple[str | None, str | None]:
        if self.kind == "toggle":
            state = opt
            if state and req.polarity == "off":
                state = "off" if state == "on" else "on"   # "turn off mute" -> unmuted
            elif state is None:
                state = req.polarity
            if state is None:
                return None, f"Should I turn {self.name} on or off?"
            if state == "toggle":
                cur = self.read()
                if cur not in ("on", "off"):
                    return None, f"I can't tell if {self.name} is on, so please say on or off."
                state = "off" if cur == "on" else "on"
            return state, None
        if self.kind == "choice":
            options = self.options()
            if not options:
                return None, f"I couldn't list the choices for {self.name}."
            if opt and not self.dynamic:
                if req.polarity == "off" and len(options) == 2:  # "turn off dark mode"
                    opt = next(v for v, _ in options if v != opt)
                return opt, None
            state, err = self._match_option(req, options)
            if state is None:
                labels = ", ".join(l for _, l in options[:6])
                return None, (err or f"Which {self.name}? For example: {labels}.")
            return state, None
        return None, None

    def _native(self, n: float, req: Request) -> float:
        if self.unit_kind == "seconds":
            return n * (req.unit_seconds or DEFAULT_TIME_UNIT_SECONDS)
        if self.unit_kind == "minutes":
            return n * (req.unit_seconds or DEFAULT_TIME_UNIT_SECONDS) / 60
        return n

    def number_for(self, req: Request) -> tuple[float | None, str | None]:
        if req.direction and (req.number is None or req.relative):
            cur = self.read(req.source)
            if cur is None:
                return None, f"I can't read the current {self.name}, so please give me a number."
            step = self._native(req.number, req) if req.number is not None else self.step
            val = cur + (step if req.direction == "up" else -step)
        elif req.number is not None:
            val = self._native(req.number, req)
        elif req.never or (req.polarity == "off" and self.min is not None and self.min <= 0):
            val = 0
        else:
            return None, f"What value should I set {self.name} to?"
        if self.min is not None:
            val = max(self.min, val)
        if self.max is not None:
            val = min(self.max, val)
        return val, None

    # ---- reading / writing ----------------------------------------------
    def _state_of(self, t: dict, raw):
        if raw is None:
            return t.get("missing")
        for state, mapped in (t.get("values") or {}).items():
            if _same(raw, mapped, t):
                return state
        return None

    def read(self, source: str | None = None):
        """'on'/'off'/option/'mixed'/number/text, or None if it can't be read."""
        if self.kind == "action":
            return None
        try:
            if self.dynamic:
                raw = _target_read(self.targets[0])
                for value, _label in self.options():
                    if raw and str(raw).lower() == value.lower():
                        return value
                return raw
            targets = [self.read_target] if self.read_target else self.targets
            if self.kind == "number":
                for t in targets:
                    raw = _target_read(t)
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        continue
                return None
            if self.kind == "text":
                return _target_read(targets[0]) if targets else None
            states = [self._state_of(t, _target_read(t)) for t in targets if t.get("values")]
            known = [s for s in states if s is not None]
            if not known:
                return None
            return known[0] if len(known) == len(states) and len(set(known)) == 1 else "mixed"
        except Exception as exc:
            log.debug("Could not read %s: %s", self.id, exc)
            return None

    def write(self, *, state=None, number=None, text=None, source=None) -> None:
        for t in self.targets:
            ctx: dict = {"state": state, "number": number, "text": text, "value": None}
            values = t.get("values")
            if self.kind in ("toggle", "choice") and not self.dynamic and values is not None:
                if state not in values:
                    continue
                ctx["value"] = values[state]
            elif self.kind == "number":
                ctx["value"] = number if t.get("float") else int(round(number))
            elif self.kind == "text":
                ctx["value"] = text
            elif self.dynamic:
                ctx["value"] = state
            _target_write(t, ctx)

    def describe(self) -> str | None:
        cur = self.read()
        if cur is None:
            return None
        if self.kind == "number":
            return self._fmt_number(cur)
        if self.dynamic or self.kind == "choice":
            return self.label_of(cur)
        return str(cur)

    def _fmt_number(self, n) -> str:
        if self.unit_kind == "seconds":
            return _human_seconds(n)
        if self.unit_kind == "minutes":
            return _human_seconds(n * 60)
        return f"{_fmt_num(n)}{' percent' if self.unit == 'percent' else (' ' + self.unit if self.unit else '')}"

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "options": ([l for _, l in self.options()][:12]
                            if self.kind == "choice" and not self.dynamic else [])}


class PowerSetting(Setting):
    """A power-plan setting discovered from `powercfg /qh` (not listed in the catalog)."""
    priority = 1
    option_scoring = False  # "sleep" alone must not select "Lid close action"

    def __init__(self, d: dict, hints: list[str]):
        self.d = d
        units = (d.get("unit") or "").lower()
        spec = {
            "id": f"power_{d['guid'][:8]}", "name": d["name"], "aliases": hints,
            "kind": "choice" if d["options"] else "number",
            "unit": "percent" if "percent" in units or "%" in units else units,
            "min": d.get("min"), "max": d.get("max"),
            "step": 10 if "percent" in units else (300 if "second" in units else max(d.get("inc") or 1, 1)),
        }
        super().__init__(spec)
        self.unit_kind = ("seconds" if "second" in units else "minutes" if "minute" in units else self.unit)
        self.option_phrases = {}

    def options(self):
        return list(self.d["options"])

    def read(self, source=None):
        fresh = {x["guid"]: x for x in _parse_power(_power_text())}.get(self.d["guid"], self.d)
        val = fresh.get("dc" if source == "dc" else "ac")
        if self.kind == "choice" and val is not None:
            return f"{val:03d}"
        return val

    def write(self, *, state=None, number=None, text=None, source=None):
        sub = (self.d.get("sub") or {}).get("guid", "SUB_NONE")
        idx = int(state) if self.kind == "choice" else int(round(number))
        exe = _resolve_exe("powercfg")
        for flag, key in (("/setacvalueindex", "ac"), ("/setdcvalueindex", "dc")):
            if source in (None, key):
                _run_checked([exe, flag, "SCHEME_CURRENT", sub, self.d["guid"], str(idx)])
        _run_checked([exe, "/setactive", "SCHEME_CURRENT"])
        _state["power"] = (0.0, [])

    def describe(self):
        fresh = {x["guid"]: x for x in _parse_power(_power_text())}.get(self.d["guid"], self.d)
        parts = []
        for key, label in (("ac", "plugged in"), ("dc", "on battery")):
            v = fresh.get(key)
            if v is None:
                continue
            parts.append(f"{self.label_of(f'{v:03d}') if self.kind == 'choice' else self._fmt_number(v)} {label}")
        return " and ".join(parts) or None


# --------------------------------------------------------------------------- #
# Power-setting discovery (patterns live in the catalog's "discovery.powercfg")
# --------------------------------------------------------------------------- #
def _power_text() -> str:
    cfg = _state["discovery"].get("powercfg") or {}
    r = _run([_resolve_exe("powercfg"), *cfg.get("query_args", ["/qh"])], timeout=30)
    return r.stdout


def _parse_power(text: str) -> list[dict]:
    cfg = _state["discovery"].get("powercfg") or {}
    pats = {k: re.compile(v, re.I) for k, v in cfg.get("patterns", {}).items()}
    need = {"subgroup", "setting", "alias", "unit", "min", "max", "inc", "opt_index", "opt_name", "ac", "dc"}
    if not need <= set(pats):
        return []
    items, sub, cur, pending = [], None, None, None
    for line in text.splitlines():
        m = pats["subgroup"].search(line)
        if m:
            sub, cur = {"guid": m.group("guid"), "name": m.group("name").strip()}, None
            continue
        m = pats["setting"].search(line)
        if m:
            cur = {"guid": m.group("guid").lower(), "name": m.group("name").strip(), "sub": sub,
                   "alias": "", "unit": "", "min": None, "max": None, "inc": None,
                   "options": [], "ac": None, "dc": None}
            items.append(cur)
            pending = None
            continue
        if cur is None:
            continue
        for key in ("alias", "unit"):
            m = pats[key].search(line)
            if m:
                cur[key] = m.group(key).strip()
        for key in ("min", "max", "inc", "ac", "dc"):
            m = pats[key].search(line)
            if m:
                cur[key] = int(m.group("v"), 16)
        m = pats["opt_index"].search(line)
        if m:
            pending = int(m.group("i"))
            continue
        m = pats["opt_name"].search(line)
        if m and pending is not None:
            cur["options"].append((f"{pending:03d}", m.group("n").strip()))
            pending = None
    return [i for i in items if i["name"]]


def _power_settings() -> list[Setting]:
    """Lazily discovered, cached; [] when unsupported or unavailable."""
    cfg = _state["discovery"].get("powercfg")
    if not cfg or not _IS_WINDOWS:
        return []
    ts, cached = _state["power"]
    if cached and time.monotonic() - ts < CACHE_SECONDS * 4:
        return cached
    try:
        hints = {k.upper(): v for k, v in cfg.get("hints", {}).items()}
        out, seen = [], set()
        for d in _parse_power(_power_text()):
            if d["name"].lower() in seen:
                continue
            seen.add(d["name"].lower())
            out.append(PowerSetting(d, hints.get(d["alias"].upper(), [])))
    except Exception as exc:
        log.warning("Power-setting discovery failed: %s", exc)
        out = []
    _state["power"] = (time.monotonic(), out)
    return out


# --------------------------------------------------------------------------- #
# Last resort: operate the Settings app itself through UI Automation
# --------------------------------------------------------------------------- #
def _ui_change(req: Request, state: str | None) -> tuple[bool, str]:
    try:
        import uiautomation as auto
    except ImportError:
        return False, "For settings without a direct API I need: pip install uiautomation"
    query = " ".join(req.subject) or req.raw
    lang = _lang()
    try:
        auto.SetGlobalSearchTimeout(UI_TIMEOUT)
        os.startfile("ms-settings:")  # generic Settings home; the page is found via its own search
        time.sleep(UI_OPEN_DELAY)
        win = auto.GetForegroundControl().GetTopLevelControl()
        box = win.EditControl(searchDepth=UI_MAX_DEPTH)
        if not box.Exists(UI_TIMEOUT, 0.5):
            return False, "I opened Settings but couldn't find its search box."
        box.Click(simulateMove=False)
        box.SendKeys("{Ctrl}a{Delete}", waitTime=0.1)
        box.SendKeys(query, interval=0.02, waitTime=UI_STEP_DELAY)
        box.SendKeys("{Down}{Enter}", waitTime=UI_OPEN_DELAY + UI_STEP_DELAY)
        win = auto.GetForegroundControl().GetTopLevelControl()

        want = [req.subject, req.subject_n]
        best, best_sc = None, 0.0
        for ctl, _depth in auto.WalkControl(win, includeTop=False, maxDepth=UI_MAX_DEPTH):
            name = ctl.Name or ""
            if not (ctl.GetTogglePattern() or ctl.GetRangeValuePattern() or ctl.GetExpandCollapsePattern()):
                continue
            names = [name]
            if _tokens(name) and all(t in lang.on | lang.off for t in _tokens(name)):
                try:
                    names.append(ctl.GetParentControl().Name or "")  # label sits on the parent
                except Exception:
                    pass
            sc = max((_fscore(q, _phrase_tokens([n])[0]) for q in want for n in names if _tokens(n)),
                     default=0.0)
            if sc > best_sc:
                best, best_sc = ctl, sc
        if best is None or best_sc < MATCH_CUTOFF:
            return False, "I opened the closest Settings page but couldn't find that control."

        label = best.Name or query
        tp = best.GetTogglePattern()
        if tp:
            is_on = tp.ToggleState == 1
            want_on = {"on": True, "off": False}.get(state or req.polarity or "")
            if want_on is None and (state or req.polarity) != "toggle":
                return False, f"Should I turn {label} on or off?"
            if want_on is None or want_on != is_on:
                tp.Toggle()
                time.sleep(0.3)
            now_on = best.GetTogglePattern().ToggleState == 1
            return True, f"{label} is now {'on' if now_on else 'off'}."
        rp = best.GetRangeValuePattern()
        if rp and req.number is not None:
            rp.SetValue(max(rp.Minimum, min(rp.Maximum, req.number)))
            return True, f"{label} set to {_fmt_num(req.number)}."
        return False, f"I found {label} in Settings but it needs a choice I can't make automatically."
    except Exception as exc:
        log.warning("UI fallback failed: %s", exc)
        return False, "I couldn't operate the Settings app for that."


def _fallback_ui(req: Request, why: str, state: str | None = None) -> tuple[bool, str]:
    if not (UI_FALLBACK and _IS_WINDOWS):
        return False, why
    ok, msg = _ui_change(req, state)
    return ok, msg if ok else f"{why} {msg}"


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def _best(req: Request) -> tuple[list[Match], str | None]:
    settings = _catalog_settings()
    ranked = _rank(req, settings)
    if not ranked or ranked[0].score < CONFIDENT_SCORE:
        extra = _power_settings()
        if extra:
            ranked = _rank(req, settings + extra)
    if not ranked or ranked[0].score < MATCH_CUTOFF:
        return ranked, None
    top = ranked[0]
    if top.score >= 0.999:
        close = [m for m in ranked if m.score >= 0.999]
    else:
        close = [m for m in ranked[:4] if m.score >= top.score - AMBIGUITY_MARGIN]
    names_seen, distinct = set(), []
    for m in close:  # same-named duplicates are not a real ambiguity
        if m.setting.name.lower() not in names_seen:
            names_seen.add(m.setting.name.lower())
            distinct.append(m)
    if len(distinct) > 1:
        viable = [m for m in distinct if m.setting.can_resolve(req, m.option)]
        if len(viable) == 1:  # only one of them can actually act on what was said
            return [viable[0]] + [m for m in ranked if m is not viable[0]], None
        pool = viable or distinct
        return ranked, "Did you mean " + " or ".join(m.setting.name for m in pool[:3]) + "?"
    return ranked, None


def _verified(st: Setting, state, number, now) -> bool:
    if now is None:
        return True
    try:
        if st.kind == "number":
            return abs(float(now) - float(number)) <= st.tol
        if st.kind in ("toggle", "choice"):
            return str(now).lower() == str(state).lower()
    except (TypeError, ValueError):
        return True
    return True


def _change(setting, value, confirmed) -> tuple[bool, str]:
    req = _parse(setting, value)
    if not req.subject:
        return False, "Which setting should I change?"
    ranked, ambiguity = _best(req)
    if not ranked or ranked[0].score < MATCH_CUTOFF:
        return _fallback_ui(req, "I don't have a direct way to change that.")
    if ambiguity:
        return False, ambiguity
    top = ranked[0]
    st = top.setting

    if st.admin and not _is_admin():
        return False, f"Changing {st.name} needs administrator rights. Please run Amigo as administrator."
    if st.confirm and not confirmed:
        return False, f"{st.name} is a sensitive setting. Please confirm and I'll change it."

    state = number = text = None
    if st.kind in ("toggle", "choice"):
        state, err = st.pick_state(req, top.option)
        if err:
            return False, err
    elif st.kind == "number":
        number, err = st.number_for(req)
        if err:
            return False, err
    elif st.kind == "text":
        text = os.path.expandvars(os.path.expanduser((req.value or "").strip().strip('"')))
        if not text:
            return False, f"What should I set {st.name} to?"
        if st.spec.get("must_exist") and not os.path.exists(text):
            return False, f"I can't find {text}."

    try:
        st.write(state=state, number=number, text=text, source=req.source)
    except Exception as exc:
        log.warning("Direct change of %s failed: %s", st.id, exc)
        return _fallback_ui(req, f"I couldn't change {st.name} directly.", state)

    _run_after(st.after)
    time.sleep(VERIFY_DELAY)
    if not _verified(st, state, number, st.read(req.source)):
        return False, (f"Windows accepted the change, but {st.name} still reads "
                       f"{st.describe()}. It may need a sign-out or restart.")

    if st.kind == "toggle":
        msg = f"{st.name} is now {state}."
    elif st.kind == "choice":
        msg = f"{st.name} set to {st.label_of(state)}."
    elif st.kind == "number":
        msg = f"{st.name} set to {st._fmt_number(number)}"
        msg += {"ac": " when plugged in.", "dc": " on battery."}.get(req.source or "", ".")
    elif st.kind == "text":
        msg = f"{st.name} updated."
    else:
        msg = f"Done: {st.name}."
    if st.note:
        msg += f" {st.note}"
    return True, msg


def change_setting(setting: str, value: str | None = None, confirmed: bool = False) -> tuple[bool, str]:
    """Changes a Windows setting by plain-language name. Returns (ok, message to speak)."""
    try:
        return _change(setting, value, confirmed)
    except Exception as exc:
        log.error("change_setting(%r, %r) failed: %s", setting, value, exc)
        return False, f"I couldn't change {setting}."


def toggle_setting(setting: str) -> tuple[bool, str]:
    return change_setting(setting, "toggle")


def get_setting(setting: str) -> tuple[bool, str]:
    """Reads a setting's current value. Returns (ok, message to speak)."""
    try:
        req = _parse(setting)
        ranked, ambiguity = _best(req)
        if not ranked or ranked[0].score < MATCH_CUTOFF:
            return False, "I don't know that setting."
        if ambiguity:
            return False, ambiguity
        st = ranked[0].setting
        desc = st.describe()
        if desc is None:
            return False, f"I can't read {st.name} on this system."
        return True, f"{st.name} is {desc}."
    except Exception as exc:
        log.error("get_setting(%r) failed: %s", setting, exc)
        return False, f"I couldn't read {setting}."


def list_settings(query: str | None = None) -> list[dict]:
    """Known settings (catalog + discovered power settings), best matches first if query given."""
    try:
        settings = _catalog_settings() + (_power_settings() if query else [])
        if not query:
            return [s.to_dict() for s in settings]
        req = _parse(query)
        return [m.setting.to_dict() for m in _rank(req, settings)[:10] if m.score >= 0.4]
    except Exception as exc:
        log.error("list_settings failed: %s", exc)
        return []


def validate_catalog() -> list[str]:
    """Static checks of the JSON catalog files. Returns a list of problems ([] = clean)."""
    problems: list[str] = []
    language, specs, discovery = _read_catalog_files()
    for key in ("on", "off", "fillers"):
        if not language.get(key):
            problems.append(f"language.{key} is empty")
    seen: set[str] = set()
    for spec in specs:
        sid = spec.get("id", "?")
        if sid in seen:
            problems.append(f"{sid}: duplicate id")
        seen.add(sid)
        kind = spec.get("kind", "toggle")
        if kind not in _VALID_KINDS:
            problems.append(f"{sid}: unknown kind {kind!r}")
        targets = spec.get("targets", [])
        if not targets:
            problems.append(f"{sid}: no targets")
        for t in targets + ([spec["read_target"]] if spec.get("read_target") else []):
            if t.get("type") not in _VALID_TARGETS:
                problems.append(f"{sid}: unknown target type {t.get('type')!r}")
            elif t["type"] == "registry" and not {"hive", "path", "name"} <= set(t):
                problems.append(f"{sid}: registry target needs hive/path/name")
            elif t["type"] == "registry" and t["hive"].upper() not in _HIVES:
                problems.append(f"{sid}: bad hive {t['hive']!r}")
            elif t["type"] == "spi" and not ({"get", "get_metric"} & set(t) or "set" in t):
                problems.append(f"{sid}: spi target needs get/set")
        if kind == "toggle":
            for t in targets:
                if t.get("values") is not None and not {"on", "off"} <= set(t["values"]):
                    problems.append(f"{sid}: toggle values need on and off")
        if kind == "choice" and not spec.get("dynamic_options"):
            if not any(t.get("values") for t in targets):
                problems.append(f"{sid}: choice needs values or dynamic_options")
        if spec.get("dynamic_options"):
            try:
                re.compile(spec["dynamic_options"]["regex"])
            except Exception as exc:
                problems.append(f"{sid}: bad dynamic_options regex ({exc})")
        if kind == "number" and spec.get("min") is not None and spec.get("max") is not None \
                and spec["min"] > spec["max"]:
            problems.append(f"{sid}: min > max")
    for key, rx in (discovery.get("powercfg", {}).get("patterns", {})).items():
        try:
            re.compile(rx)
        except Exception as exc:
            problems.append(f"discovery.powercfg.{key}: bad regex ({exc})")
    return problems
