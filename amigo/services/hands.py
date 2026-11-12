"""
Python bridge to the native Windows `roamies-hands` Rust helper.
Provides fast UI Automation (UIA) inspection, window/browser context, OCR hit-testing,
and structured GUI actions (`click_text`, `fill`, `type`, `press`, `scroll`).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("amigo.hands")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_PATHS = (
    _REPO_ROOT / "amigo" / "lib" / "roamies-hands.exe",
    _REPO_ROOT / "roamies-win-extract" / "hands-win" / "target" / "release" / "roamies-hands.exe",
    _REPO_ROOT / "roamies-win-extract" / "hands-win" / "target" / "debug" / "roamies-hands.exe",
)


def find_binary() -> str | None:
    """Return the path to `roamies-hands.exe` if available, else None."""
    for env_key in ("AMIGO_HANDS_BIN", "ROAMIES_HANDS"):
        val = os.environ.get(env_key, "").strip()
        if val and os.path.isfile(val):
            return val
    for path in _CANDIDATE_PATHS:
        if path.is_file():
            return str(path)
    return shutil.which("roamies-hands")


def is_available() -> bool:
    """Return True if the `roamies-hands` binary is present on disk."""
    return find_binary() is not None


def _creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def run_hands(
    args: list[str],
    *,
    timeout: float = 30.0,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute `roamies-hands` with *args* and return its parsed JSON response."""
    binary = find_binary()
    if not binary:
        return {
            "ok": False,
            "missing_binary": True,
            "error": (
                "roamies-hands.exe is not built yet. Install Visual Studio Build Tools "
                "(Desktop development with C++) and run `cargo build --release` in "
                "roamies-win-extract/hands-win."
            ),
        }

    cmd = [binary, *args]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"ok": False, "error": f"roamies-hands timed out after {timeout:.0f}s."}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to run roamies-hands: {exc}"}

    if on_progress and stderr:
        for line in stderr.splitlines():
            if line.startswith("@"):
                try:
                    on_progress(json.loads(line[1:]))
                except Exception:
                    pass

    last_line = ""
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if stripped:
            last_line = stripped

    if not last_line:
        err_msg = (stderr or "").strip() or f"roamies-hands exited with code {proc.returncode}"
        return {"ok": False, "error": err_msg}

    try:
        data = json.loads(last_line)
        return data if isinstance(data, dict) else {"ok": False, "error": "Invalid JSON output from roamies-hands."}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Bad JSON from roamies-hands: {exc}"}


def windows() -> dict[str, Any]:
    return run_hands(["windows"], timeout=5.0)


def front() -> dict[str, Any]:
    return run_hands(["front"], timeout=3.0)


def locate(target: str = "front") -> dict[str, Any]:
    return run_hands(["locate", target], timeout=5.0)


def capture(out_path: str, target: str = "front", max_px: int | None = 1568) -> dict[str, Any]:
    args = ["capture", out_path, target]
    if max_px is not None:
        args.append(str(int(max_px)))
    return run_hands(args, timeout=15.0)


def ui(target: str = "front", *, controls_only: bool = False) -> dict[str, Any]:
    args = ["ui", target]
    if controls_only:
        args.append("controls")
    return run_hands(args, timeout=15.0)


def find_text(target: str, text: str, *, scroll: bool = True) -> dict[str, Any]:
    args = ["find-text", target, text]
    if scroll:
        args.append("scroll")
    return run_hands(args, timeout=15.0)


def ocr_find(target: str, text: str, *, all_matches: bool = False) -> dict[str, Any]:
    args = ["ocr-find", target, text]
    if all_matches:
        args.append("all")
    return run_hands(args, timeout=15.0)


def act(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return run_hands(["act", json.dumps(plan, ensure_ascii=False)], timeout=60.0, on_progress=on_progress)


def describe_active_window(max_controls: int = 40) -> dict[str, Any]:
    """Return structured foreground app, URL, UIA controls, and visible text if `hands` is available."""
    if not is_available():
        return {"ok": False, "missing_binary": True}

    front_res = front()
    ui_res = ui("front")
    if not ui_res.get("ok"):
        return front_res if front_res.get("ok") else ui_res

    win = ui_res.get("window") or {}
    controls = ui_res.get("controls") or []
    compact_controls = []
    for ctrl in controls[:max_controls]:
        if not isinstance(ctrl, dict):
            continue
        label = ctrl.get("name") or ctrl.get("title") or ctrl.get("value") or ""
        role = ctrl.get("role") or ctrl.get("type") or ""
        cid = ctrl.get("id")
        if label or role:
            compact_controls.append({"id": cid, "role": role, "label": str(label)[:80]})

    return {
        "ok": True,
        "app": front_res.get("app") or win.get("app") or "",
        "window_title": front_res.get("title") or win.get("title") or "",
        "url": front_res.get("url") or "",
        "controls": compact_controls,
        "texts": ui_res.get("texts") or "",
        "elsewhere": ui_res.get("elsewhere") or "",
    }
