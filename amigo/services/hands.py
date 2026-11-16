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


def select_area(out_path: str) -> dict[str, Any]:
    """Let the user interactively drag-select a screen region and save it to *out_path*."""
    return run_hands(["select", out_path], timeout=60.0)


def refine(target: str, boxes: list[list[float]]) -> dict[str, Any]:
    """Refine coarse pixel boxes in *target* using `roamies-hands refine`."""
    return run_hands(["refine", target, json.dumps(boxes)], timeout=10.0)


def get_screen_bounds() -> tuple[float, float]:
    """Return logical screen `(width, height)` via the Rust native `roamies-hands` binary."""
    if is_available():
        try:
            res = windows()
            win_list = res.get("windows") if isinstance(res, dict) else None
            if isinstance(win_list, list) and win_list:
                max_x = max(float(w["bounds"][0]) + float(w["bounds"][2]) for w in win_list if isinstance(w, dict) and isinstance(w.get("bounds"), list) and len(w["bounds"]) == 4)
                max_y = max(float(w["bounds"][1]) + float(w["bounds"][3]) for w in win_list if isinstance(w, dict) and isinstance(w.get("bounds"), list) and len(w["bounds"]) == 4)
                if max_x > 0 and max_y > 0:
                    return max_x, max_y
        except Exception:
            pass
    return 1920.0, 1080.0


_AMIGO_APP_NAMES = frozenset({"amigo", "python", "pythonw", "py", "amigo assistant"})
_last_non_amigo_app: str | None = None
_last_non_amigo_time: float = 0.0


def remember_target_app(app_name: str) -> None:
    """Record the last non-Amigo foreground app name for use in screen targeting."""
    global _last_non_amigo_app, _last_non_amigo_time
    import time
    if app_name and app_name.lower().split(".")[0] not in _AMIGO_APP_NAMES:
        _last_non_amigo_app = app_name
        _last_non_amigo_time = time.time()


def get_target_app() -> str | None:
    """Return the most recent non-Amigo foreground app, or None if stale (>120s)."""
    import time
    if _last_non_amigo_app and (time.time() - _last_non_amigo_time) < 120.0:
        return _last_non_amigo_app
    return None


def get_front_non_amigo() -> dict[str, Any]:
    """Return the foreground window info, skipping Amigo/Python windows."""
    win_res = windows()
    win_list = win_res.get("windows") if isinstance(win_res, dict) else None
    if isinstance(win_list, list):
        for w in win_list:
            if not isinstance(w, dict):
                continue
            app = str(w.get("app") or "").lower().split(".")[0]
            if app and app not in _AMIGO_APP_NAMES and w.get("focused") is not False:
                remember_target_app(str(w.get("app") or ""))
                return {"ok": True, "app": w.get("app"), "title": w.get("title"), "bounds": w.get("bounds")}
    cached = get_target_app()
    if cached:
        return {"ok": True, "app": cached, "title": "", "bounds": None}
    return {"ok": False}


def shot_map(bounds: list[float], img_w: float, img_h: float) -> dict[str, Any]:
    """Build a coordinate map from screenshot pixels (`img_w` x `img_h`) to screen `bounds` `[x, y, w, h]`."""
    x, y, w, h = [float(v) for v in bounds[:4]]
    return {
        "origin": [x, y],
        "sx": w / max(1.0, float(img_w)),
        "sy": h / max(1.0, float(img_h)),
        "size": [float(img_w), float(img_h)],
    }


def shot_fits(bounds: list[float], img_w: float, img_h: float) -> bool:
    """Return True if an image of `img_w`x`img_h` matches the aspect ratio of `bounds`."""
    _, _, w, h = [float(v) for v in bounds[:4]]
    if w <= 0 or h <= 0 or img_w <= 0 or img_h <= 0:
        return False
    return abs((w / h) / (img_w / img_h) - 1.0) <= 0.02


def to_screen(mapping: dict[str, Any], x: float, y: float) -> tuple[int, int]:
    """Map screenshot pixel `(x, y)` back to screen coordinates using *mapping*."""
    ox, oy = mapping["origin"]
    return round(ox + float(x) * mapping["sx"]), round(oy + float(y) * mapping["sy"])


def normalize_grid_coords(
    x: float | int,
    y: float | int,
    bounds: list[float] | tuple[float, float, float, float] | None = None,
) -> tuple[int, int]:
    """Map coordinate (x, y) to absolute screen pixels.

    Seamlessly supports:
    - Standard normalized floats in [0.0, 1.0] (maps to bounds)
    - Local Qwen3-VL and Qwen3.5 0-to-1000 coordinate grid (maps to bounds)
    - Absolute pixel coordinates (> 1000 or raw)
    """
    fx, fy = float(x), float(y)
    if bounds and len(bounds) >= 4:
        ox, oy, bw, bh = [float(v) for v in bounds[:4]]
    else:
        sw, sh = get_screen_bounds()
        ox, oy, bw, bh = 0.0, 0.0, float(sw), float(sh)

    # 1. 0.0 to 1.0 scale
    if 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
        return round(ox + fx * bw), round(oy + fy * bh)

    # 2. Qwen3-VL / Qwen3.5 0-to-1000 grid
    if 0.0 <= fx <= 1000.0 and 0.0 <= fy <= 1000.0:
        return round(ox + (fx / 1000.0) * bw), round(oy + (fy / 1000.0) * bh)

    # 3. Already absolute pixel coordinates
    return round(fx), round(fy)


def snap_rect(rect: list[float], controls: list[dict[str, Any] | list[float]]) -> list[float] | None:
    """Snap a coarse screen `[x, y, w, h]` rect to the best-overlapping UIA control rect."""
    x, y, w, h = [float(v) for v in rect[:4]]
    area = w * h
    if area <= 0:
        return None
    best: list[float] | None = None
    best_fit = 0.35
    for item in controls or []:
        b = item.get("bounds") if isinstance(item, dict) else item
        if not isinstance(b, (list, tuple)) or len(b) < 4:
            continue
        bx, by, bw, bh = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        if bw <= 0 or bh <= 0:
            continue
        ratio = (bw * bh) / area
        if ratio < 0.4 or ratio > 2.5:
            continue
        ix = max(0.0, min(x + w, bx + bw) - max(x, bx))
        iy = max(0.0, min(y + h, by + bh) - max(y, by))
        inter = ix * iy
        union = area + bw * bh - inter
        fit = inter / union if union > 0 else 0.0
        if fit > best_fit:
            best_fit = fit
            best = [bx, by, bw, bh]
    return best


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
    if front_res.get("ok"):
        remember_target_app(str(front_res.get("app") or ""))
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
