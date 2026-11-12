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
        if "lay" not in out:
            out["lay"] = {
                "layers": [[str(n["id"])] for n in out["nodes"] if isinstance(n, dict) and n.get("id")],
                "via": {},
                "back": [],
            }
    return out


def _generate_visual_diagram_beats(topic: str) -> tuple[list[dict[str, Any]], str]:
    """Generate Roamies `ink.js` / `ink3d.js` visual diagram beats and a short spoken summary via the local LLM."""
    from amigo.core.llm_agent import query_local_llm, sanitize_for_tts

    prompt = (
        f"Explain '{topic}' visually as compact JSON with keys: "
        f"'summary' (1-2 short sentences), "
        f"'visual_type' ('flow' | 'table' | 'timeline' | 'bars' | 'math'), "
        f"'steps' (3-4 short stage strings in order, for flow/timeline), "
        f"'parts' (3-4 key component/item strings), "
        f"'columns' (optional 2-3 headers for table), 'rows' (optional 2-4 rows for table), "
        f"'bars' (optional array of {{\"label\":\"...\",\"value\":10}} for bars), "
        f"'tex' (optional LaTeX formula for math). Output ONLY valid JSON."
    )
    raw = query_local_llm(prompt, max_tokens=380)
    summary = f"Here is a visual breakdown of {topic}."
    marks: list[dict[str, Any]] = []
    tones = ["sky", "amber", "coral", "teal"]

    if raw:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if parsed.get("summary"):
                    summary = sanitize_for_tts(str(parsed["summary"])) or summary

                vtype = str(parsed.get("visual_type") or "flow").strip().lower()
                raw_steps = parsed.get("steps") or []
                if vtype == "table" and isinstance(parsed.get("columns"), list) and isinstance(parsed.get("rows"), list) and parsed["rows"]:
                    cols = [str(c)[:30] for c in parsed["columns"][:6]]
                    rows = [[str(cell)[:80] for cell in r[:len(cols)]] for r in parsed["rows"][:12] if isinstance(r, list)]
                    if cols and rows:
                        marks.append({"kind": "table", "part": f"p{len(marks) + 1}", "title": topic.title()[:40], "columns": cols, "rows": rows})
                elif vtype == "bars" and isinstance(parsed.get("bars"), list) and parsed["bars"]:
                    bars = [
                        {"label": str(b.get("label") or "")[:30], "value": float(b.get("value") or 0), "tone": tones[i % len(tones)]}
                        for i, b in enumerate(parsed["bars"][:12]) if isinstance(b, dict) and b.get("label")
                    ]
                    if bars:
                        marks.append({"kind": "bars", "part": f"p{len(marks) + 1}", "title": topic.title()[:40], "bars": bars})
                elif vtype == "timeline" and isinstance(raw_steps, list) and len(raw_steps) >= 2:
                    events = [{"when": f"Step {i + 1}", "text": str(st)[:60]} for i, st in enumerate(raw_steps[:10])]
                    marks.append({"kind": "timeline", "part": f"p{len(marks) + 1}", "title": topic.title()[:40], "events": events})
                elif vtype == "math" and parsed.get("tex"):
                    marks.append({"kind": "math", "part": f"p{len(marks) + 1}", "title": topic.title()[:40], "tex": str(parsed["tex"])[:300]})

                if not marks and isinstance(raw_steps, list) and len(raw_steps) >= 2:
                    nodes = []
                    links = []
                    for idx, st in enumerate(raw_steps[:5]):
                        nid = f"s{idx + 1}"
                        nodes.append({"id": nid, "text": str(st)[:60], "tone": tones[idx % len(tones)]})
                        if idx > 0:
                            links.append({"from": f"s{idx}", "to": nid, "flow": True})
                    marks.append({
                        "kind": "flow",
                        "part": f"p{len(marks) + 1}",
                        "title": f"{topic.title()[:28]} Flow",
                        "nodes": nodes,
                        "links": links,
                        "lay": {"layers": [[n["id"]] for n in nodes], "via": {}, "back": []},
                    })

                raw_parts = parsed.get("parts") or []
                if isinstance(raw_parts, list) and raw_parts:
                    items = []
                    for idx, p in enumerate(raw_parts[:5]):
                        if isinstance(p, dict):
                            lbl = str(p.get("label") or p.get("name") or f"Part {idx + 1}").strip()
                            items.append(lbl[:80])
                        elif isinstance(p, str) and p.strip():
                            items.append(p.strip()[:80])
                    if items:
                        marks.append({
                            "kind": "list",
                            "part": f"p{len(marks) + 1}",
                            "title": "Key Components",
                            "items": items,
                            "tone": "amber",
                        })
            except Exception:
                pass

    marks.append({"kind": "card", "part": f"p{len(marks) + 1}", "title": topic.title()[:60], "text": summary, "tone": "teal"})
    return [{"marks": marks, "caption": summary}], summary


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

    # Resolve real on-screen bounds via Rust `hands` when label/target is given without explicit pixel coords
    resolved_rect: list[float] | None = None
    if label and normalized and x == 500 and y == 500:
        try:
            from amigo.services import hands
            hit = hands.find_text("front", str(label), scroll=False)
            matches = hit.get("matches") or []
            if not matches:
                hit = hands.ocr_find("front", str(label))
                matches = hit.get("matches") or []
            if matches and isinstance(matches[0], dict) and isinstance(matches[0].get("bounds"), list):
                b = matches[0]["bounds"]
                if len(b) == 4:
                    resolved_rect = [float(b[0]), float(b[1]), max(24.0, float(b[2])), max(24.0, float(b[3]))]
        except Exception:
            pass

    px, py = _to_screen_points(x, y, normalized)
    tone = _color_to_tone(color)

    if act == "arrow":
        if resolved_rect:
            rx, ry, rw, rh = resolved_rect
            tx, ty = rx + rw / 2, ry + rh / 2
            mark: Dict[str, Any] = {"kind": "arrow", "points": [[tx + 90, ty - 70], [tx, ty]], "tone": tone}
        else:
            px2, py2 = _to_screen_points(x2 if x2 is not None else x + 120, y2 if y2 is not None else y - 80, normalized)
            mark = {"kind": "arrow", "points": [[px2, py2], [px, py]], "tone": tone}
    elif act == "box":
        if resolved_rect:
            mark = {"kind": "rect", "at": {"rect": resolved_rect}, "tone": tone}
        else:
            px2, py2 = _to_screen_points(x2 if x2 is not None else x + 120, y2 if y2 is not None else y + 60, normalized)
            bx, by = min(px, px2), min(py, py2)
            bw, bh = max(24.0, abs(px2 - px)), max(24.0, abs(py2 - py))
            mark = {"kind": "rect", "at": {"rect": [bx, by, bw, bh]}, "tone": tone}
    elif act == "underline":
        if resolved_rect:
            mark = {"kind": "underline", "at": {"rect": resolved_rect}, "tone": tone}
        else:
            w = max(40.0, abs(_to_screen_points(x2, y, normalized)[0] - px)) if x2 is not None else 140.0
            mark = {"kind": "underline", "at": {"rect": [px - w / 2, py - 12, w, 24]}, "tone": tone}
    else:
        if resolved_rect:
            mark = {"kind": "circle", "at": {"rect": resolved_rect}, "tone": tone}
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
