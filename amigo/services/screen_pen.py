"""
Click-Through Screen Pen & Visual Overlay Tool for Amigo Voice Assistant.
Draws non-blocking, click-through circles, arrows, boxes, underlines, and floating labels on Windows.
Zero external dependencies (uses built-in tkinter + Win32 ctypes).
Language-agnostic and fully dynamic.
"""

from __future__ import annotations

import ctypes
import math
import queue
import threading
import tkinter as tk
from typing import Any, Dict, Optional

# Ensure accurate pixel coordinates on high-DPI Windows displays (125%, 150%, etc.)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class ScreenPenOverlay:
    """
    Manages a fullscreen, transparent, click-through Windows overlay in a background thread.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.cmd_queue: queue.Queue = queue.Queue()
        try:
            self.screen_w = ctypes.windll.user32.GetSystemMetrics(0) or 1920
            self.screen_h = ctypes.windll.user32.GetSystemMetrics(1) or 1080
        except Exception:
            self.screen_w = 1920
            self.screen_h = 1080

        # Start GUI thread as a daemon so it closes automatically with your main app
        self.thread = threading.Thread(target=self._run_overlay_loop, daemon=True)
        self.thread.start()

    def _make_click_through(self, hwnd: int):
        """Applies Win32 extended styles so mouse clicks pass right through the overlay."""
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080  # Hides overlay from Alt+Tab and Taskbar
        WS_EX_NOACTIVATE = 0x08000000  # Prevents stealing keyboard focus

        try:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        except Exception:
            pass

    def _run_overlay_loop(self):
        try:
            self.root = tk.Tk()
            self.root.overrideredirect(True)
            self.root.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
            self.root.attributes("-topmost", True)

            # Chroma-key transparency color (pure magenta-black unlikely to conflict)
            transparent_key = "#010101"
            self.root.config(bg=transparent_key)
            try:
                self.root.attributes("-transparentcolor", transparent_key)
            except Exception:
                pass

            self.canvas = tk.Canvas(
                self.root,
                width=self.screen_w,
                height=self.screen_h,
                bg=transparent_key,
                highlightthickness=0,
                bd=0
            )
            self.canvas.pack(fill=tk.BOTH, expand=True)

            self.root.update_idletasks()
            try:
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                self._make_click_through(hwnd)
            except Exception:
                pass

            self._poll_queue()
            self.root.mainloop()
        except Exception:
            # Graceful headless / initialization fallback
            pass

    def _poll_queue(self):
        try:
            while True:
                task = self.cmd_queue.get_nowait()
                self._execute_draw(task)
        except queue.Empty:
            pass
        except Exception:
            pass
        if hasattr(self, "root"):
            self.root.after(30, self._poll_queue)

    def _scale(self, val: int, max_dim: int, normalized: bool) -> int:
        if normalized:
            return int(max(0, min(1000, val)) / 1000.0 * max_dim)
        return int(val)

    def _execute_draw(self, cmd: Dict[str, Any]):
        if not hasattr(self, "canvas"):
            return
        action = cmd.get("action", "circle")
        if action == "clear":
            self.canvas.delete("all")
            return

        norm = cmd.get("normalized", True)
        x = self._scale(cmd.get("x", 500), self.screen_w, norm)
        y = self._scale(cmd.get("y", 500), self.screen_h, norm)
        x2 = self._scale(cmd.get("x2", x + 120), self.screen_w, norm) if cmd.get("x2") is not None else None
        y2 = self._scale(cmd.get("y2", y + 60), self.screen_h, norm) if cmd.get("y2") is not None else None

        color = cmd.get("color", "#00F0FF")  # Default neon cyan
        radius = int(cmd.get("radius", 45))
        duration_ms = int(cmd.get("duration", 4.0) * 1000)
        label = cmd.get("label")

        group_tag = f"item_{id(cmd)}"

        # 1. Draw the requested visual shape
        if action == "circle":
            self.canvas.create_oval(
                x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2,
                outline="#000000", width=6, tags=group_tag
            )
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                outline=color, width=4, tags=group_tag
            )
            self._animate_pulse(group_tag, x, y, radius, color, steps=6)

        elif action == "box":
            bx2 = x2 if x2 is not None else x + radius * 2
            by2 = y2 if y2 is not None else y + radius
            self.canvas.create_rectangle(
                x - 1, y - 1, bx2 + 1, by2 + 1,
                outline="#000000", width=6, tags=group_tag
            )
            self.canvas.create_rectangle(
                x, y, bx2, by2,
                outline=color, width=4, tags=group_tag
            )

        elif action == "underline":
            ux2 = x2 if x2 is not None else x + 160
            self.canvas.create_line(
                x, y + 1, ux2, y + 1,
                fill="#000000", width=7, capstyle=tk.ROUND, tags=group_tag
            )
            self.canvas.create_line(
                x, y, ux2, y,
                fill=color, width=4, capstyle=tk.ROUND, tags=group_tag
            )

        elif action == "arrow":
            start_x = x2 if x2 is not None else max(20, x - 110)
            start_y = y2 if y2 is not None else max(20, y - 110)
            self.canvas.create_line(
                start_x, start_y, x, y,
                fill="#000000", width=8, arrow=tk.LAST, arrowshape=(18, 22, 8),
                capstyle=tk.ROUND, tags=group_tag
            )
            self.canvas.create_line(
                start_x, start_y, x, y,
                fill=color, width=5, arrow=tk.LAST, arrowshape=(16, 20, 6),
                capstyle=tk.ROUND, tags=group_tag
            )

        # 2. Draw optional floating text badge next to the annotation
        if label:
            badge_y = max(30, y - radius - 28) if action == "circle" else max(30, y - 32)
            text_id = self.canvas.create_text(
                x, badge_y, text=f" {label} ",
                fill="#FFFFFF", font=("Segoe UI", 12, "bold"),
                tags=group_tag
            )
            bbox = self.canvas.bbox(text_id)
            if bbox:
                pad_x, pad_y = 8, 4
                bg_id = self.canvas.create_rectangle(
                    bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y,
                    fill="#18181B", outline=color, width=2, tags=group_tag
                )
                self.canvas.tag_raise(text_id, bg_id)

        # 3. Auto-fade / clear after duration
        if duration_ms > 0:
            self.root.after(duration_ms, lambda: self.canvas.delete(group_tag))

    def _animate_pulse(self, tag: str, x: int, y: int, base_r: int, color: str, steps: int, step: int = 0):
        """Creates a quick attention-grabbing ripple effect when a circle first appears."""
        if not hasattr(self, "canvas") or step >= steps:
            if hasattr(self, "canvas"):
                self.canvas.delete(f"{tag}_pulse")
            return
        self.canvas.delete(f"{tag}_pulse")
        r = base_r + int(math.sin(step / steps * math.pi) * 14)
        self.canvas.create_oval(
            x - r, y - r, x + r, y + r,
            outline=color, width=2, dash=(4, 4), tags=(tag, f"{tag}_pulse")
        )
        self.root.after(60, lambda: self._animate_pulse(tag, x, y, base_r, color, steps, step + 1))

    def dispatch(self, payload: Dict[str, Any]):
        self.cmd_queue.put(payload)


# =====================================================================
# PLUG-AND-PLAY LLM TOOL SCHEMA & DISPATCHER
# =====================================================================

PEN_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "screen_pen_annotate",
        "description": (
            "Draws visual annotations (circle, arrow, box, underline) and floating text labels "
            "directly on the user's screen to point out UI elements, buttons, or areas of interest. "
            "Click-through, non-blocking, and language-agnostic."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["circle", "arrow", "box", "underline", "clear"],
                    "description": "Shape to draw on screen, or 'clear' to wipe all current annotations."
                },
                "x": {
                    "type": "integer",
                    "description": "Target X coordinate (0-1000 normalized across screen width by default, or exact pixel if normalized=false)."
                },
                "y": {
                    "type": "integer",
                    "description": "Target Y coordinate (0-1000 normalized across screen height by default, or exact pixel if normalized=false)."
                },
                "x2": {
                    "type": "integer",
                    "description": "Optional secondary X coordinate (used for arrow starting point or box bottom-right corner)."
                },
                "y2": {
                    "type": "integer",
                    "description": "Optional secondary Y coordinate (used for arrow starting point or box bottom-right corner)."
                },
                "label": {
                    "type": "string",
                    "description": "Optional floating text annotation to display above or beside the drawn shape."
                },
                "color": {
                    "type": "string",
                    "description": "Hex color string for the annotation (e.g. '#00F0FF' for neon cyan, '#FF3366' for pink, '#FFCC00' for yellow)."
                },
                "duration": {
                    "type": "number",
                    "description": "Duration in seconds before the visual annotation automatically disappears (default 4.0)."
                },
                "normalized": {
                    "type": "boolean",
                    "description": "Whether x and y coordinates are normalized to 0-1000 scale (default true). Set false for raw pixel values."
                }
            },
            "required": ["action"]
        }
    }
}


def screen_pen_annotate(
    action: str,
    x: int = 500,
    y: int = 500,
    x2: Optional[int] = None,
    y2: Optional[int] = None,
    label: Optional[str] = None,
    color: str = "#00F0FF",
    duration: float = 4.0,
    normalized: bool = True
) -> Dict[str, Any]:
    """
    Entrypoint function to bind to your LLM tool registry.
    """
    valid_actions = {"circle", "arrow", "box", "underline", "clear"}
    act = action.lower()
    if act not in valid_actions:
        return {"status": "error", "code": "INVALID_ACTION", "supported": list(valid_actions)}

    try:
        overlay = ScreenPenOverlay()
        overlay.dispatch({
            "action": act,
            "x": x,
            "y": y,
            "x2": x2,
            "y2": y2,
            "label": label,
            "color": color,
            "duration": duration,
            "normalized": normalized
        })
        return {
            "status": "success",
            "drawn": act,
            "coordinates": {"x": x, "y": y, "normalized": normalized},
            "auto_fade_seconds": duration,
            "label": label
        }
    except Exception as exc:
        return {"status": "error", "code": "OVERLAY_ERROR", "details": str(exc)}

