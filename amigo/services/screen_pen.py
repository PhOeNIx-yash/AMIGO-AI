"""
Screen Pen & Visual Overlay Bridge for Amigo Voice Assistant.
Drives the JavaScript-native Roamies overlay (`roamies-win-extract/overlay/desktop.html` + `ink.js`)
via SSE events instead of Python tkinter.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from typing import Any, Dict, Optional

_SUBSCRIBERS_LOCK = threading.Lock()
_SUBSCRIBERS: list[queue.Queue] = []
_LAST_EVENT: tuple[float, Dict[str, Any]] | None = None

_COLOR_TONES = {
    "#00F0FF": "teal",
    "#78FFE2": "teal",
    "#FF3366": "coral",
    "#FF6B6B": "red",
    "#FFCC00": "amber",
    "#FFCF5C": "amber",
    "#B9A4FF": "lilac",
    "#7CC8FF": "sky",
    "#B6F36B": "lime",
    "#E8FFFA": "white",
}


def subscribe_overlay_events() -> queue.Queue:
    """Register an SSE subscriber queue for Roamies JS overlay events."""
    q: queue.Queue = queue.Queue(maxsize=64)
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.append(q)
        if _LAST_EVENT is not None and (time.time() - _LAST_EVENT[0]) < 15.0:
            try:
                q.put_nowait(_LAST_EVENT[1])
            except queue.Full:
                pass
    return q


def unsubscribe_overlay_events(q: queue.Queue) -> None:
    """Remove an SSE subscriber queue."""
    with _SUBSCRIBERS_LOCK:
        if q in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(q)


def push_overlay_event(event: Dict[str, Any]) -> int:
    """Broadcast a Roamies overlay event (`ink`, `ink_clear`, etc.) to connected JS overlays."""
    global _LAST_EVENT
    with _SUBSCRIBERS_LOCK:
        _LAST_EVENT = (time.time(), event) if event.get("type") != "ink_clear" else None
        targets = list(_SUBSCRIBERS)
    delivered = 0
    for q in targets:
        try:
            q.put_nowait(event)
            delivered += 1
        except queue.Full:
            pass
    return delivered


def _to_screen_points(x: int | float, y: int | float, normalized: bool) -> tuple[float, float]:
    if not normalized:
        return float(x), float(y)
    # 0-1000 normalized -> 1920x1080 logical points fallback when screen dimensions aren't passed
    return round((float(x) / 1000.0) * 1920.0, 1), round((float(y) / 1000.0) * 1080.0, 1)


def _color_to_tone(color: str) -> str:
    return _COLOR_TONES.get(str(color or "").strip().upper(), "teal")


PEN_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "screen_pen_annotate",
        "description": (
            "Draws visual annotations (circle, arrow, box, underline) and floating text labels "
            "using the Roamies JavaScript ink overlay to point out UI elements or areas of interest."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["circle", "arrow", "box", "underline", "clear"],
                    "description": "Shape to draw on screen, or 'clear' to wipe all current annotations.",
                },
                "x": {"type": "integer", "description": "Target X coordinate."},
                "y": {"type": "integer", "description": "Target Y coordinate."},
                "x2": {"type": "integer", "description": "Optional secondary X coordinate."},
                "y2": {"type": "integer", "description": "Optional secondary Y coordinate."},
                "label": {"type": "string", "description": "Optional floating text label."},
                "color": {"type": "string", "description": "Hex color string (mapped to Roamies ink tones)."},
                "duration": {"type": "number", "description": "Duration in seconds (default 4.0)."},
                "normalized": {"type": "boolean", "description": "Whether x/y are 0-1000 normalized (default true)."},
            },
            "required": ["action"],
        },
    },
}


def _sanitize_mark(mk: dict[str, Any]) -> dict[str, Any]:
    """Normalize LLM-generated mark fields into strict Roamies `ink.js`/`ink3d.js` shapes."""
    out = dict(mk)
    for key in ("kind", "tone"):
        if isinstance(out.get(key), str) and "|" in out[key]:
            out[key] = out[key].split("|", 1)[0].strip()
    at_val = out.get("at")
    if isinstance(at_val, dict) and "rect" not in at_val and all(k in at_val for k in ("x", "y")):
        out["at"] = {"rect": [float(at_val["x"]), float(at_val["y"]), float(at_val.get("w", 160)), float(at_val.get("h", 160))]}
    if isinstance(out.get("parts"), list):
        clean_parts = []
        for p in out["parts"]:
            if not isinstance(p, dict):
                continue
            cp = dict(p)
            for key in ("shape", "tone"):
                if isinstance(cp.get(key), str) and "|" in cp[key]:
                    cp[key] = cp[key].split("|", 1)[0].strip()
            if isinstance(cp.get("motion"), str):
                cp["motion"] = {"do": cp["motion"].split("|", 1)[0].strip()}
            clean_parts.append(cp)
        out["parts"] = clean_parts
    if out.get("kind") == "flow" and isinstance(out.get("nodes"), list):
        node_ids = {str(n.get("id")) for n in out["nodes"] if isinstance(n, dict) and n.get("id")}
        if isinstance(out.get("links"), list):
            for lk in out["links"]:
                if isinstance(lk, dict):
                    for end in ("from", "to"):
                        nid = str(lk.get(end) or "").strip()
                        if nid and nid not in node_ids:
                            node_ids.add(nid)
                            out["nodes"].append({"id": nid, "text": nid.title(), "tone": "teal"})
    return out


def _generate_visual_diagram_beats(topic: str) -> tuple[list[dict[str, Any]], str]:
    """Generate Roamies `ink.js` / `ink3d.js` visual diagram beats and a short spoken summary via the local LLM."""
    from amigo.core.llm_agent import query_local_llm, sanitize_for_tts

    prompt = (
        f"Explain '{topic}' visually as compact JSON with 3 keys: "
        f"'summary' (1-2 short sentences), "
        f"'parts' (3 objects: {{\"shape\":\"sphere|torus|cone|cylinder|box\",\"label\":\"2-word name\",\"tone\":\"sky|amber|coral|teal\"}}), "
        f"'steps' (3-4 short stage strings in order). Output ONLY valid JSON."
    )
    raw = query_local_llm(prompt, max_tokens=320)
    summary = f"Here is a visual breakdown of {topic}."
    marks: list[dict[str, Any]] = []
    offsets = [(0, 0.7, 0), (0, -0.7, 0), (1.3, 0, 0), (-1.3, 0, 0)]
    motions = [{"do": "pulse"}, {"do": "spin"}, {"do": "orbit"}, {"do": "spin"}]
    tones = ["sky", "amber", "coral", "teal"]

    if raw:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if parsed.get("summary"):
                    summary = sanitize_for_tts(str(parsed["summary"])) or summary

                raw_parts = parsed.get("parts") or []
                valid_shapes = {"box", "sphere", "cylinder", "cone", "torus"}
                default_shapes = ["cylinder", "torus", "cone", "box"]
                if isinstance(raw_parts, list) and raw_parts:
                    model_parts = []
                    for idx, p in enumerate(raw_parts[:4]):
                        if not isinstance(p, dict):
                            continue
                        raw_shape = str(p.get("shape") or "").split("|", 1)[0].strip().lower()
                        shape = raw_shape if raw_shape in valid_shapes else default_shapes[idx % len(default_shapes)]
                        tone = str(p.get("tone") or tones[idx % len(tones)]).split("|", 1)[0].strip()
                        label = str(p.get("label") or f"Part {idx + 1}")[:24]
                        model_parts.append({
                            "shape": shape,
                            "at": list(offsets[idx % len(offsets)]),
                            "size": [1.0, 1.0, 0.5] if shape == "torus" else [1.0, 1.1, 1.0],
                            "tone": tone,
                            "label": label,
                            "motion": motions[idx % len(motions)],
                        })
                    if model_parts:
                        marks.append({"kind": "model", "part": f"p{len(marks) + 1}", "title": topic.title()[:40], "parts": model_parts})

                raw_steps = parsed.get("steps") or []
                if isinstance(raw_steps, list) and len(raw_steps) >= 2:
                    nodes = []
                    links = []
                    for idx, st in enumerate(raw_steps[:5]):
                        nid = f"s{idx + 1}"
                        nodes.append({"id": nid, "text": str(st)[:60], "tone": tones[idx % len(tones)]})
                        if idx > 0:
                            links.append({"from": f"s{idx}", "to": nid, "flow": True})
                    marks.append({"kind": "flow", "part": f"p{len(marks) + 1}", "title": f"{topic.title()[:28]} Flow", "nodes": nodes, "links": links})
            except Exception:
                pass

    marks.append({"kind": "card", "part": f"p{len(marks) + 1}", "title": topic.title()[:60], "text": summary, "tone": "teal"})
    return [{"marks": marks}], summary


def screen_pen_annotate(
    action: str,
    x: int = 500,
    y: int = 500,
    x2: Optional[int] = None,
    y2: Optional[int] = None,
    label: Optional[str] = None,
    color: str = "#00F0FF",
    duration: float = 4.0,
    normalized: bool = True,
    topic: Optional[str] = None,
    beats: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Emit a Roamies JavaScript `ink.js` / `ink3d.js` beat event for the requested screen annotation or diagram."""
    valid_actions = {"circle", "arrow", "box", "underline", "diagram", "clear"}
    act = str(action or "").strip().lower()
    if act not in valid_actions:
        return {"status": "error", "code": "INVALID_ACTION", "supported": list(valid_actions)}

    if act == "clear":
        event: Dict[str, Any] = {"type": "ink_clear", "all": True}
        subscribers = push_overlay_event(event)
        return {"status": "success", "drawn": "clear", "engine": "roamies-ink-js", "subscribers": subscribers, "event": event}

    if act == "diagram":
        diag_topic = str(topic or label or "concept").strip()
        if isinstance(beats, list) and beats:
            diag_beats = beats
            summary = f"Drew a visual diagram for {diag_topic}."
        else:
            diag_beats, summary = _generate_visual_diagram_beats(diag_topic)
        event = {
            "type": "ink",
            "id": f"ink-{int(time.time() * 1000)}",
            "seg": 0,
            "last": True,
            "fade": max(18.0, float(duration)),
            "beats": diag_beats,
        }
        subscribers = push_overlay_event(event)
        return {
            "status": "success",
            "drawn": "diagram",
            "engine": "roamies-ink-js",
            "topic": diag_topic,
            "summary": summary,
            "subscribers": subscribers,
            "event": event,
        }

    px, py = _to_screen_points(x, y, normalized)
    tone = _color_to_tone(color)

    if act == "arrow":
        px2, py2 = _to_screen_points(x2 if x2 is not None else x + 120, y2 if y2 is not None else y - 80, normalized)
        mark: Dict[str, Any] = {"kind": "arrow", "points": [[px2, py2], [px, py]], "tone": tone}
    elif act == "box":
        px2, py2 = _to_screen_points(x2 if x2 is not None else x + 120, y2 if y2 is not None else y + 60, normalized)
        bx, by = min(px, px2), min(py, py2)
        bw, bh = max(24.0, abs(px2 - px)), max(24.0, abs(py2 - py))
        mark = {"kind": "rect", "at": {"rect": [bx, by, bw, bh]}, "tone": tone}
    elif act == "underline":
        w = max(40.0, abs(_to_screen_points(x2, y, normalized)[0] - px)) if x2 is not None else 140.0
        mark = {"kind": "underline", "at": {"rect": [px - w / 2, py - 12, w, 24]}, "tone": tone}
    else:
        mark = {"kind": "circle", "at": {"rect": [px - 45, py - 45, 90, 90]}, "tone": tone}

    if label:
        mark["label"] = str(label)[:40]
    mark["part"] = "p1"

    event = {
        "type": "ink",
        "id": f"ink-{int(time.time() * 1000)}",
        "seg": 0,
        "last": True,
        "fade": max(2.0, float(duration)),
        "beats": [{"marks": [mark]}],
    }
    subscribers = push_overlay_event(event)
    return {
        "status": "success",
        "drawn": act,
        "engine": "roamies-ink-js",
        "coordinates": {"x": x, "y": y, "normalized": normalized},
        "auto_fade_seconds": duration,
        "label": label,
        "subscribers": subscribers,
        "event": event,
    }


class ScreenPenOverlay:
    """Compatibility wrapper forwarding overlay dispatch calls to Roamies JS `ink.js`."""

    def dispatch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return screen_pen_annotate(
            action=str(payload.get("action") or "circle"),
            x=payload.get("x", 500),
            y=payload.get("y", 500),
            x2=payload.get("x2"),
            y2=payload.get("y2"),
            label=payload.get("label"),
            color=str(payload.get("color") or "#00F0FF"),
            duration=float(payload.get("duration", 4.0)),
            normalized=bool(payload.get("normalized", True)),
        )
