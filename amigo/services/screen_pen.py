"""
Screen Pen & Visual Overlay Bridge for Amigo Voice Assistant.
Drives the JavaScript-native Roamies overlay (`desktop.html` + `ink.js` + `ink3d.js`)
with support for branching flowcharts, 2D graphs, LaTeX math trees, molecules,
maps, quizzes, tables, timelines, bar charts, geometry, 3D models, and live UI guides.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("amigo.screen_pen")

_SUBSCRIBERS_LOCK = threading.Lock()
_SUBSCRIBERS: list[queue.Queue] = []
# Stores (timestamp, fade_seconds, wrapped_event) for recent non-transient draw events
_LAST_EVENT: tuple[float, float, Dict[str, Any]] | None = None

_VALID_TONES = {"teal", "amber", "coral", "lilac", "sky", "lime", "red", "white", "accent"}
_VALID_3D_SHAPES = {"sphere", "torus", "cone", "cylinder", "box"}
_DIAGRAM_KINDS = {
    "diagram", "flow", "graph", "bars", "table", "timeline",
    "math", "geometry", "molecule", "map", "quiz", "model", "card", "note", "list", "mindmap",
    "ticket", "medal",
}
_SCREEN_MARK_KINDS = {
    "circle", "ring", "arrow", "box", "rect", "underline",
    "strike", "highlight", "spotlight", "point", "target", "hover",
}
_INTERACTIVE_ROLES = {"edit", "button", "hyperlink", "menuitem", "tabitem", "checkbox", "combobox"}

# Exact index order from world.js so `drawMap` can shade countries by name
_WORLD_COUNTRIES = [
    "Fiji", "Tanzania", "Western Sahara", "Canada", "United States", "Kazakhstan", "Uzbekistan",
    "Papua New Guinea", "Indonesia", "Argentina", "Chile", "Democratic Republic of the Congo",
    "Somalia", "Kenya", "Sudan", "Chad", "Haiti", "Dominican Republic", "Russia", "Bahamas",
    "Falkland Islands", "Norway", "Greenland", "French Southern and Antarctic Lands", "East Timor",
    "South Africa", "Lesotho", "Mexico", "Uruguay", "Brazil", "Bolivia", "Peru", "Colombia",
    "Panama", "Costa Rica", "Nicaragua", "Honduras", "El Salvador", "Guatemala", "Belize",
    "Venezuela", "Guyana", "Suriname", "France", "Ecuador", "Puerto Rico", "Jamaica", "Cuba",
    "Zimbabwe", "Botswana", "Namibia", "Senegal", "Mali", "Mauritania", "Benin", "Niger",
    "Nigeria", "Cameroon", "Togo", "Ghana", "Ivory Coast", "Guinea", "Guinea-Bissau", "Liberia",
    "Sierra Leone", "Burkina Faso", "Central African Republic", "Republic of the Congo", "Gabon",
    "Equatorial Guinea", "Zambia", "Malawi", "Mozambique", "Eswatini", "Angola", "Burundi",
    "Israel", "Lebanon", "Madagascar", "Palestine", "Gambia", "Tunisia", "Algeria", "Jordan",
    "United Arab Emirates", "Qatar", "Kuwait", "Iraq", "Oman", "Vanuatu", "Cambodia", "Thailand",
    "Laos", "Myanmar", "Vietnam", "North Korea", "South Korea", "Mongolia", "India", "Bangladesh",
    "Bhutan", "Nepal", "Pakistan", "Afghanistan", "Tajikistan", "Kyrgyzstan", "Turkmenistan",
    "Iran", "Syria", "Armenia", "Sweden", "Belarus", "Ukraine", "Poland", "Austria", "Hungary",
    "Moldova", "Romania", "Lithuania", "Latvia", "Estonia", "Germany", "Bulgaria", "Greece",
    "Turkey", "Albania", "Croatia", "Switzerland", "Luxembourg", "Belgium", "Netherlands",
    "Portugal", "Spain", "Ireland", "New Caledonia", "Solomon Islands", "New Zealand", "Australia",
    "Sri Lanka", "China", "Taiwan", "Italy", "Denmark", "United Kingdom", "Iceland", "Azerbaijan",
    "Georgia", "Philippines", "Malaysia", "Brunei", "Slovenia", "Finland", "Slovakia",
    "Czech Republic", "Eritrea", "Japan", "Paraguay", "Yemen", "Saudi Arabia", "Antarctica",
    "Northern Cyprus", "Cyprus", "Morocco", "Egypt", "Libya", "Ethiopia", "Djibouti", "Somaliland",
    "Uganda", "Rwanda", "Bosnia and Herzegovina", "North Macedonia", "Serbia", "Montenegro",
    "Kosovo", "Trinidad and Tobago", "South Sudan",
]
_COUNTRY_INDEX = {name.lower(): idx for idx, name in enumerate(_WORLD_COUNTRIES)}


def _safe_int(val: Any, default: int = 0, lo: int | None = None, hi: int | None = None) -> int:
    try:
        parsed = int(float(val))
    except (ValueError, TypeError):
        parsed = default
    if lo is not None:
        parsed = max(lo, parsed)
    if hi is not None:
        parsed = min(hi, parsed)
    return parsed


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def subscribe_overlay_events() -> queue.Queue:
    """Register an SSE subscriber queue for Roamies JS overlay events."""
    q: queue.Queue = queue.Queue(maxsize=64)
    now = time.time()
    replay_event: Dict[str, Any] | None = None

    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.append(q)
        if _LAST_EVENT is not None:
            ev_time, ev_fade, ev_payload = _LAST_EVENT
            age = now - ev_time
            if age < min(15.0, max(1.0, ev_fade)):
                replay_event = ev_payload

    if replay_event is not None:
        try:
            q.put_nowait(replay_event)
        except queue.Full:
            logger.debug("[Overlay Subscribe] Replay skipped: subscriber queue full")
    return q


def unsubscribe_overlay_events(q: queue.Queue) -> None:
    """Remove an SSE subscriber queue."""
    with _SUBSCRIBERS_LOCK:
        if q in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(q)


def _wrap_roamie_host_event(inner_event: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap an ink event into the `{kind: 'agent', e: {...}}` envelope expected by `main.rs` / `desktop.html`."""
    etype = inner_event.get("type")
    if etype == "ink_clear":
        out = dict(inner_event)
        out.update({"kind": "agent", "e": {"type": "clear", "all": bool(inner_event.get("all", True))}})
        return out
    if etype == "ink":
        draw_payload = {k: v for k, v in inner_event.items() if k != "type"}
        draw_payload["type"] = "draw"
        out = dict(inner_event)
        out.update({"kind": "agent", "e": draw_payload})
        return out
    if etype == "reminders":
        return {
            "kind": "list",
            "today": inner_event.get("today", []),
            "later": inner_event.get("later", []),
            "seconds": inner_event.get("seconds", 25),
        }
    if etype == "emote":
        return {"kind": "agent", "e": {"type": "emote", "action": str(inner_event.get("action") or "wave")}}
    if etype == "sfx":
        return {"kind": "sfx", "name": str(inner_event.get("name") or "pop")}
    if etype == "tool_start":
        return {
            "kind": "agent",
            "e": {
                "type": "tool",
                "id": str(inner_event.get("id") or "t1"),
                "anim": inner_event.get("anim") or {},
            },
        }
    if etype == "tool_end":
        return {
            "kind": "agent",
            "e": {
                "type": "tool_end",
                "id": str(inner_event.get("id") or "t1"),
                "isError": bool(inner_event.get("is_error", False)),
            },
        }
    if etype == "edit":
        return {
            "kind": "agent",
            "e": {
                "type": "draw",
                "id": f"edit-{int(time.time() * 1000)}",
                "seg": 0,
                "last": True,
                "edits": [{
                    "do": str(inner_event.get("do") or "erase"),
                    "part": str(inner_event.get("part") or ""),
                    "tone": str(inner_event.get("tone") or "amber"),
                    "dx": _safe_float(inner_event.get("dx"), 0.0),
                    "dy": _safe_float(inner_event.get("dy"), 0.0),
                }],
            },
        }
    if etype == "pin":
        return {
            "kind": "agent",
            "e": {
                "type": "pin",
                "part": str(inner_event.get("part") or ""),
                "on": bool(inner_event.get("on", True)),
            },
        }
    return {"kind": "agent", "e": inner_event}


def push_overlay_event(event: Dict[str, Any]) -> int:
    """Broadcast a Roamies overlay event to all connected JS overlays."""
    global _LAST_EVENT
    envelope = _wrap_roamie_host_event(event)
    etype = event.get("type")
    is_transient = etype in ("ink_clear", "sfx", "emote", "tool_start", "tool_end", "edit", "pin")

    with _SUBSCRIBERS_LOCK:
        if etype == "ink_clear":
            _LAST_EVENT = None
        elif not is_transient:
            fade_sec = _safe_float(event.get("fade"), 16.0)
            _LAST_EVENT = (time.time(), fade_sec, envelope)
        targets = list(_SUBSCRIBERS)

    delivered = 0
    for q in targets:
        try:
            q.put_nowait(envelope)
            delivered += 1
        except queue.Full:
            logger.debug("[Overlay Push] Subscriber queue full; dropping event '%s'", etype)
    return delivered


def emit_avatar_act(action: str) -> int:
    """Trigger companion avatar animation (wave, hype, remind, motivate, upset)."""
    return push_overlay_event({"type": "emote", "action": str(action or "wave").strip()})


def emit_overlay_sfx(name: str = "pop") -> int:
    """Trigger a Roamies WebAudio `sfx.js` sound effect (pop, tick, pip, whoosh, scan, blip, focus, hype, page)."""
    return push_overlay_event({"type": "sfx", "name": str(name or "pop").strip()})


def emit_tool_anim(tool_name: str, label: str = "", variant: str = "") -> str:
    """Show a live animated tool badge beside the avatar while a tool runs."""
    tid = f"tool_{int(time.time() * 1000)}"
    push_overlay_event({
        "type": "tool_start",
        "id": tid,
        "anim": {"tool": tool_name, "label": label or tool_name, "variant": variant},
    })
    return tid


def end_tool_anim(tool_id: str, is_error: bool = False) -> None:
    """Complete a live tool badge with checkmark or error pip."""
    push_overlay_event({
        "type": "tool_end",
        "id": tool_id,
        "is_error": is_error,
    })


def _emit_single_mark_widget(
    prefix: str,
    title: str,
    mark_payload: Dict[str, Any],
    duration: float,
    min_fade: float,
    part: str | None = None,
    sfx_name: str | None = None,
) -> str:
    pid = str(part or f"{prefix}_{int(time.time() * 1000)}")
    mark = _sanitize_mark({"part": pid, **mark_payload})
    push_overlay_event({
        "type": "ink",
        "id": pid,
        "seg": 0,
        "last": True,
        "fade": max(min_fade, float(duration)),
        "beats": [{"voiced": True, "caption": title, "marks": [mark]}],
    })
    if sfx_name:
        emit_overlay_sfx(sfx_name)
    return pid


def emit_roamies_quiz(
    title: str,
    questions: list[dict[str, Any]],
    duration: float = 60.0,
    pinned: bool = False,
    part: str | None = None,
) -> str:
    """Emit an interactive desktop quiz directly onto the transparent Roamies overlay."""
    return _emit_single_mark_widget(
        prefix="quiz",
        title=title,
        duration=duration,
        min_fade=30.0,
        part=part,
        sfx_name="page",
        mark_payload={
            "kind": "quiz",
            "title": title,
            "questions": questions,
            "pinned": bool(pinned),
        },
    )


def emit_roamies_ticket(
    title: str,
    text: str = "",
    n: int | None = None,
    label: str = "Try it",
    action: bool = True,
    duration: float = 30.0,
    pinned: bool = False,
    part: str | None = None,
) -> str:
    """Emit an actionable notched job ticket with tear line and interactive action button."""
    return _emit_single_mark_widget(
        prefix="ticket",
        title=title,
        duration=duration,
        min_fade=15.0,
        part=part,
        sfx_name="page",
        mark_payload={
            "kind": "ticket",
            "title": title,
            "text": text,
            "n": n,
            "label": label,
            "action": action,
            "pinned": bool(pinned),
        },
    )


def emit_roamies_medal(
    title: str,
    text: str = "",
    duration: float = 20.0,
    part: str | None = None,
) -> str:
    """Emit an animated goal/milestone medal with ribbon, star trace, and hype celebration."""
    return _emit_single_mark_widget(
        prefix="medal",
        title=title,
        duration=duration,
        min_fade=12.0,
        part=part,
        sfx_name=None,
        mark_payload={
            "kind": "medal",
            "title": title,
            "text": text,
        },
    )


def emit_overlay_edit(
    action: str,
    part: str = "",
    tone: str = "amber",
    dx: float = 0.0,
    dy: float = 0.0,
) -> int:
    """Emit an in-place live canvas edit (erase, recolor, move, clear) without redraw flickering."""
    return push_overlay_event({
        "type": "edit",
        "do": str(action).strip().lower(),
        "part": str(part).strip(),
        "tone": str(tone).strip(),
        "dx": _safe_float(dx, 0.0),
        "dy": _safe_float(dy, 0.0),
    })


def emit_pin_part(part: str, on: bool = True) -> int:
    """Pin or unpin a card so it stays anchored through window changes and Esc."""
    return push_overlay_event({"type": "pin", "part": str(part).strip(), "on": bool(on)})


def _get_viewport_bounds() -> tuple[float, float]:
    """Query active display bounds dynamically from `hands` without hardcoded screen dimensions."""
    try:
        from amigo.services.hands import get_screen_bounds
        sw, sh = get_screen_bounds()
        return (float(sw) if sw and sw > 0 else 1920.0), (float(sh) if sh and sh > 0 else 1080.0)
    except Exception as exc:
        logger.debug("[Viewport] get_screen_bounds failed, using default: %s", exc)
        return 1920.0, 1080.0


def _to_screen_points(x: int | float, y: int | float, normalized: bool) -> tuple[float, float]:
    fx, fy = _safe_float(x, 0.0), _safe_float(y, 0.0)
    if not normalized:
        return round(fx, 1), round(fy, 1)
    try:
        from amigo.services.hands import normalize_grid_coords
        nx, ny = normalize_grid_coords(fx, fy)
        return float(nx), float(ny)
    except Exception as exc:
        logger.debug("[Coords] normalize_grid_coords fallback: %s", exc)
        sw, sh = _get_viewport_bounds()
        if 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
            return round(fx * sw, 1), round(fy * sh, 1)
        return round((fx / 1000.0) * sw, 1), round((fy / 1000.0) * sh, 1)


def _resolve_target_rect(label: str, app: str | None = None) -> list[float] | None:
    """Resolve real on-screen bounds `[x, y, w, h]` for *label* via Rust `hands` (`app` / `front` -> `screen`)."""
    target_text = str(label or "").strip().strip("\"'")
    if not target_text:
        return None

    t_low = target_text.lower()
    tokens = [t for t in t_low.split() if len(t) >= 2]

    if app:
        ui_target = f"app:{app}"
        search_targets = [f"app:{app}", "front", "screen"]
    else:
        prev_app = None
        try:
            from amigo.services.hands import get_target_app
            prev_app = get_target_app()
        except Exception as exc:
            logger.debug("[Target Rect] get_target_app failed: %s", exc)
        ui_target = f"app:{prev_app}" if prev_app else "front"
        search_targets = [f"app:{prev_app}", "front", "screen"] if prev_app else ["front", "screen"]

    try:
        from amigo.services import hands

        sw, sh = _get_viewport_bounds()

        def _is_invalid_box(bx: float, by: float, bw: float, bh: float) -> bool:
            if by < 0.0 or bx < 0.0:
                return True
            if bx < 30.0 and by < 50.0 and (bw > sw * 0.5 or bh > sh * 0.5):
                return True
            if bw > sw * 0.85 or bh > sh * 0.85:
                return True
            return False

        ui_res = hands.ui(ui_target)
        if ui_res.get("ok"):
            controls = ui_res.get("controls") or []
            best_viewport_c = None
            best_viewport_score = -1.0
            best_chrome_c = None
            best_chrome_score = -1.0

            for c in controls:
                if not isinstance(c, dict):
                    continue
                b = c.get("bounds")
                if not isinstance(b, list) or len(b) != 4 or b[2] <= 0 or b[3] <= 0:
                    continue
                bx, by, bw, bh = float(b[0]), float(b[1]), float(b[2]), float(b[3])
                if _is_invalid_box(bx, by, bw, bh):
                    continue
                lbl = str(c.get("label") or c.get("name") or c.get("value") or "").strip().lower()
                if not lbl:
                    continue

                score = 0.0
                if lbl == t_low:
                    score = 100.0
                elif t_low in lbl:
                    score = 70.0
                elif tokens:
                    c_matched = sum(1 for tok in tokens if tok in lbl)
                    if c_matched > 0:
                        score = 40.0 * (c_matched / len(tokens))

                if score <= 0.0:
                    continue

                role = str(c.get("role") or c.get("control_type") or "").strip().lower()
                if role in _INTERACTIVE_ROLES:
                    score *= 1.35

                scale_penalty = 1.0 / (1.0 + (bw * bh) / 150000.0)
                final_score = score * scale_penalty

                if by >= 115.0:
                    if final_score > best_viewport_score:
                        best_viewport_score = final_score
                        best_viewport_c = c
                else:
                    if final_score > best_chrome_score:
                        best_chrome_score = final_score
                        best_chrome_c = c

            chosen_c = (
                best_viewport_c
                if (best_viewport_c and best_viewport_score >= 15.0)
                else (best_chrome_c if best_chrome_score >= 20.0 else None)
            )
            if chosen_c:
                b = chosen_c["bounds"]
                bw = min(float(b[2]), max(80.0, float(len(target_text) * 14.0))) if float(b[2]) > 220.0 else float(b[2])
                return [float(b[0]), float(b[1]), bw, float(b[3])]

        queries = [target_text]
        if len(tokens) > 1:
            for tok in tokens:
                if tok not in queries:
                    queries.append(tok)

        chrome_match = None
        for q in queries:
            for tgt in search_targets:
                hit = hands.ocr_find(tgt, q)
                matches = hit.get("matches") or []
                if not matches:
                    hit = hands.find_text(tgt, q, scroll=False)
                    matches = hit.get("matches") or []
                for m in matches:
                    if isinstance(m, dict) and isinstance(m.get("bounds"), list):
                        b = m["bounds"]
                        if len(b) == 4 and b[2] > 0 and b[3] > 0:
                            bx, by, bw, bh = float(b[0]), float(b[1]), float(b[2]), float(b[3])
                            if not _is_invalid_box(bx, by, bw, bh):
                                adj_w = min(bw, max(80.0, float(len(q) * 14.0))) if bw > 220.0 else bw
                                if by >= 115.0:
                                    return [bx, by, adj_w, bh]
                                if chrome_match is None:
                                    chrome_match = [bx, by, adj_w, bh]
        if chrome_match is not None:
            return chrome_match
    except Exception as exc:
        logger.debug("[Target Rect] Failed resolving '%s': %s", target_text, exc)
    return None


def _resolve_tone(color: str | None) -> str:
    """Map user/LLM color names or hex values into Roamies CSS palette tokens."""
    c = str(color or "").strip().lower()
    if c in _VALID_TONES:
        return c
    if c.startswith("#") and len(c) == 7:
        try:
            r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        except ValueError:
            return "teal"
        if r > 200 and g < 110:
            return "coral" if b > 90 else "red"
        if r > 200 and g > 170 and b < 110:
            return "amber"
        if g > 200 and r < 140 and b > 180:
            return "teal"
        if b > 200 and r > 140:
            return "lilac"
        if b > 200:
            return "sky"
        if g > 200:
            return "lime"
    return "teal"


# ---------------------------------------------------------------------------
# Converters for Roamies `ink.js` specialized internal schemas
# ---------------------------------------------------------------------------

def _build_flow_layout(nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute a multi-layer topological layout (`layers`, `via`, `back`) required by `ink.js` `drawFlow`."""
    node_ids = [str(n["id"]) for n in nodes if isinstance(n, dict) and n.get("id")]
    id_set = set(node_ids)
    adj: dict[str, list[tuple[str, int]]] = {nid: [] for nid in node_ids}
    for k, lk in enumerate(links):
        u, v = str(lk.get("from") or ""), str(lk.get("to") or "")
        if u in id_set and v in id_set and u != v:
            adj[u].append((v, k))

    visited: set[str] = set()
    on_stack: set[str] = set()
    back_edges: list[int] = []

    def _dfs(u: str) -> None:
        visited.add(u)
        on_stack.add(u)
        for v, edge_idx in adj.get(u, []):
            if v in on_stack:
                back_edges.append(edge_idx)
            elif v not in visited:
                _dfs(v)
        on_stack.remove(u)

    for nid in node_ids:
        if nid not in visited:
            _dfs(nid)

    back_set = set(back_edges)
    rank: dict[str, int] = {nid: 0 for nid in node_ids}
    for _ in range(len(node_ids)):
        changed = False
        for k, lk in enumerate(links):
            if k in back_set:
                continue
            u, v = str(lk.get("from") or ""), str(lk.get("to") or "")
            if u in rank and v in rank and rank[v] < rank[u] + 1:
                rank[v] = rank[u] + 1
                changed = True
        if not changed:
            break

    max_rank = max(rank.values(), default=0)
    layers: list[list[str]] = [[] for _ in range(max_rank + 1)]
    for nid in node_ids:
        layers[rank[nid]].append(nid)
    layers = [layer for layer in layers if layer]

    for r_idx, layer in enumerate(layers):
        for nid in layer:
            rank[nid] = r_idx

    via: dict[str, list[str]] = {}
    for k, lk in enumerate(links):
        u, v = str(lk.get("from") or ""), str(lk.get("to") or "")
        if u not in rank or v not in rank or u == v:
            continue
        r_start = rank[v] if k in back_set else rank[u]
        r_end = rank[u] if k in back_set else rank[v]
        if r_end - r_start > 1:
            lanes = []
            for r_mid in range(r_start + 1, r_end):
                lane_id = f"__lane_{k}_{r_mid}"
                layers[r_mid].append(lane_id)
                lanes.append(lane_id)
            via[str(k)] = lanes

    return {"layers": layers, "via": via, "back": back_edges}


_GREEK_AND_OPS = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\epsilon": "ε",
    r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ", r"\pi": "π", r"\sigma": "σ",
    r"\tau": "τ", r"\phi": "φ", r"\omega": "ω", r"\Delta": "Δ", r"\Sigma": "∑",
    r"\Omega": "Ω", r"\int": "∫", r"\sum": "∑", r"\prod": "∏", r"\infty": "∞",
    r"\pm": "±", r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\neq": "≠",
    r"\leq": "≤", r"\geq": "≥", r"\approx": "≈", r"\rightarrow": "→", r"\to": "→",
}
_REL_CHARS = {"=", "≠", "≤", "≥", "≈", "→", "<", ">"}
_OP_CHARS = {"+", "-", "−", "±", "×", "·", "÷", "/"}
_FN_NAMES = {"sin", "cos", "tan", "log", "ln", "lim", "max", "min", "exp"}


def _tex_to_tree(expr: str) -> dict[str, Any]:
    """Convert a LaTeX or plain math string into the AST format expected by `ink.js` `texBox` / `drawMath`."""
    s = str(expr or "").strip()
    for k, v in _GREEK_AND_OPS.items():
        s = s.replace(k, v)

    def _extract_group(text: str, pos: int) -> tuple[str, int]:
        if pos >= len(text):
            return "", pos
        if text[pos] == "{":
            depth = 0
            for i in range(pos, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[pos + 1 : i], i + 1
            return text[pos + 1 :], len(text)
        return text[pos], pos + 1

    def _parse(text: str) -> dict[str, Any]:
        children: list[dict[str, Any]] = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch.isspace():
                i += 1
                continue
            if text.startswith(r"\frac", i):
                num_s, i = _extract_group(text, i + 5)
                den_s, i = _extract_group(text, i)
                children.append({"t": "frac", "a": _parse(num_s), "b": _parse(den_s)})
            elif text.startswith(r"\sqrt", i):
                i += 5
                deg_node = None
                if i < n and text[i] == "[":
                    end_b = text.find("]", i)
                    if end_b != -1:
                        deg_node = _parse(text[i + 1 : end_b])
                        i = end_b + 1
                rad_s, i = _extract_group(text, i)
                children.append({"t": "sqrt", "a": _parse(rad_s), "n": deg_node})
            elif text.startswith(r"\boxed", i):
                inner_s, i = _extract_group(text, i + 6)
                children.append({"t": "box", "a": _parse(inner_s)})
            elif text.startswith(r"\text", i):
                txt_s, i = _extract_group(text, i + 5)
                children.append({"t": "text", "s": txt_s})
            elif ch in ("^", "_"):
                base = children.pop() if children else {"t": "s", "s": ""}
                sup_node = base.get("sup") if base.get("t") == "script" else None
                sub_node = base.get("sub") if base.get("t") == "script" else None
                real_base = base.get("a") if base.get("t") == "script" else base
                val_s, i = _extract_group(text, i + 1)
                if ch == "^":
                    sup_node = _parse(val_s)
                else:
                    sub_node = _parse(val_s)
                children.append({"t": "script", "a": real_base, "sup": sup_node, "sub": sub_node})
            elif ch == "{":
                grp_s, i = _extract_group(text, i)
                children.append(_parse(grp_s))
            else:
                matched_fn = next(
                    (fn for fn in _FN_NAMES if text.startswith(fn, i) and (i + len(fn) == n or not text[i + len(fn)].isalpha())),
                    None,
                )
                if matched_fn:
                    children.append({"t": "s", "s": matched_fn, "k": "fn"})
                    i += len(matched_fn)
                else:
                    if ch == "\\":
                        i += 1
                        if i >= n:
                            break
                        ch = text[i]
                    kind = "rel" if ch in _REL_CHARS else ("op" if ch in _OP_CHARS else "ord")
                    children.append({"t": "s", "s": "−" if ch == "-" else ch, "k": kind})
                    i += 1
        return children[0] if len(children) == 1 else {"t": "row", "c": children}

    return _parse(s)


_VALENCE = {"C": 4, "N": 3, "O": 2, "S": 2, "P": 3, "F": 1, "Cl": 1, "Br": 1, "I": 1}


def _parse_smiles_to_mol(smiles: str) -> dict[str, Any]:
    """Parse a SMILES string (including bracketed atoms/charges and ring closures) into a molecule dict."""
    atoms: list[dict[str, Any]] = []
    bonds: list[dict[str, Any]] = []
    stack: list[int] = []
    rings: dict[str, tuple[int, float]] = {}
    prev: Optional[int] = None
    next_order: float = 1.0
    i = 0
    s = str(smiles or "").strip()

    while i < len(s):
        ch = s[i]
        if ch == "(":
            if prev is not None:
                stack.append(prev)
            i += 1
        elif ch == ")":
            if stack:
                prev = stack.pop()
            i += 1
        elif ch in ("=", "#", "-"):
            next_order = 2.0 if ch == "=" else (3.0 if ch == "#" else 1.0)
            i += 1
        elif ch == "[":
            end_b = s.find("]", i + 1)
            if end_b == -1:
                i += 1
                continue
            token = s[i + 1 : end_b].strip()
            i = end_b + 1
            el_match = re.match(r"^([A-Za-z]{1,2})", token)
            raw_el = el_match.group(1) if el_match else "C"
            arom = raw_el.islower()
            el = raw_el.capitalize()
            h_match = re.search(r"H(\d*)", token)
            explicit_h = _safe_int(h_match.group(1) or 1, 1) if h_match else 0
            charge = 0
            if token.endswith("++"):
                charge = 2
            elif token.endswith("--"):
                charge = -2
            elif "+" in token:
                c_m = re.search(r"\+(\d*)", token)
                charge = _safe_int(c_m.group(1) if c_m and c_m.group(1) else 1, 1)
            elif "-" in token[1:]:
                c_m = re.search(r"-(\d*)", token[1:])
                charge = -_safe_int(c_m.group(1) if c_m and c_m.group(1) else 1, 1)
            idx = len(atoms)
            atoms.append({"el": el, "h": explicit_h, "charge": charge, "_arom": arom, "_explicit_h": h_match is not None})
            if prev is not None:
                order = 1.5 if (arom and atoms[prev].get("_arom") and next_order == 1.0) else next_order
                bonds.append({"a": prev, "b": idx, "order": order})
            prev = idx
            next_order = 1.0
        elif ch.isdigit():
            if prev is not None:
                if ch in rings:
                    r_atom, r_ord = rings.pop(ch)
                    effective_ord = next_order if next_order != 1.0 else r_ord
                    if r_atom != prev:
                        bonds.append({"a": r_atom, "b": prev, "order": effective_ord})
                else:
                    rings[ch] = (prev, next_order)
            next_order = 1.0
            i += 1
        elif ch.isalpha():
            arom = ch.islower()
            if i + 1 < len(s) and s[i : i + 2].lower() in ("cl", "br"):
                el = s[i : i + 2].capitalize()
                i += 2
            else:
                el = ch.upper()
                i += 1
            idx = len(atoms)
            atoms.append({"el": el, "h": 0, "charge": 0, "_arom": arom, "_explicit_h": False})
            if prev is not None:
                order = 1.5 if (arom and atoms[prev].get("_arom") and next_order == 1.0) else next_order
                bonds.append({"a": prev, "b": idx, "order": order})
            prev = idx
            next_order = 1.0
        else:
            i += 1

    for idx, a in enumerate(atoms):
        explicit_h = a.pop("_explicit_h", False)
        a.pop("_arom", None)
        if not explicit_h and a["el"] != "C" and a["el"] in _VALENCE:
            used = sum(int(round(b["order"])) for b in bonds if b["a"] == idx or b["b"] == idx)
            a["h"] = max(0, _VALENCE[a["el"]] - used + int(a.get("charge", 0)))

    return {"atoms": atoms, "bonds": bonds} if atoms else {"atoms": [{"el": "O", "h": 2, "charge": 0}], "bonds": []}


# ---------------------------------------------------------------------------
# Per-Kind Mark Sanitizers
# ---------------------------------------------------------------------------

def _vec3(val: Any, default: list[float], lo: float, hi: float) -> list[float]:
    if isinstance(val, (list, tuple)) and len(val) == 3:
        try:
            return [max(lo, min(hi, float(v))) for v in val]
        except (TypeError, ValueError):
            pass
    return list(default)


def _sanitize_model_mark(out: dict[str, Any]) -> None:
    if not isinstance(out.get("parts"), list):
        return
    clean_parts = []
    count = max(1, min(8, len(out["parts"])))
    for idx, p in enumerate(out["parts"][:8]):
        if not isinstance(p, dict):
            continue
        cp = dict(p)
        shape = str(cp.get("shape") or "sphere").split("|", 1)[0].strip().lower()
        cp["shape"] = shape if shape in _VALID_3D_SHAPES else "sphere"
        cp["tone"] = _resolve_tone(cp.get("tone"))
        default_y = round(1.2 * (1.0 - (2.0 * idx / max(1, count - 1))), 2) if count > 1 else 0.0
        raw_at = cp.get("at") if "at" in cp else cp.pop("pos", None)
        cp["at"] = _vec3(raw_at, [0.0, default_y, 0.0], -3.0, 3.0)
        cp["size"] = _vec3(cp.get("size"), [0.6, 0.6, 0.6], 0.1, 3.0)
        if isinstance(cp.get("rot"), (list, tuple)) and len(cp["rot"]) == 3:
            cp["rot"] = _vec3(cp["rot"], [0.0, 0.0, 0.0], -360.0, 360.0)
        if isinstance(cp.get("motion"), str):
            cp["motion"] = {"do": cp["motion"].split("|", 1)[0].strip()}
        clean_parts.append(cp)
    out["parts"] = clean_parts


def _sanitize_flow_mark(out: dict[str, Any]) -> None:
    if not isinstance(out.get("nodes"), list):
        return
    clean_nodes = []
    node_ids: set[str] = set()
    for n in out["nodes"]:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        nid = str(n["id"]).strip()
        node_ids.add(nid)
        shape = str(n.get("shape") or "box").lower()
        if shape not in {"box", "round", "diamond"}:
            shape = "diamond" if "?" in str(n.get("text") or "") else "box"
        clean_nodes.append({
            "id": nid,
            "text": str(n.get("text") or nid)[:72],
            "shape": shape,
            "tone": _resolve_tone(n.get("tone")),
        })
    clean_links = []
    if isinstance(out.get("links"), list):
        for lk in out["links"]:
            if not isinstance(lk, dict):
                continue
            u = str(lk.get("from") or "").strip()
            v = str(lk.get("to") or "").strip()
            if not u or not v or u == v:
                continue
            for end in (u, v):
                if end not in node_ids:
                    node_ids.add(end)
                    clean_nodes.append({"id": end, "text": end.replace("_", " ").title(), "shape": "box", "tone": "teal"})
            clean_links.append({
                "from": u,
                "to": v,
                "label": str(lk.get("label") or "")[:32] if lk.get("label") else None,
                "flow": bool(lk.get("flow", True)),
            })
    out["nodes"] = clean_nodes
    out["links"] = clean_links
    out["lay"] = _build_flow_layout(clean_nodes, clean_links)


def _sanitize_math_mark(out: dict[str, Any]) -> None:
    raw_steps = out.get("steps") or []
    if not raw_steps and out.get("tex"):
        raw_steps = [{"tex": str(out["tex"]), "note": ""}]
    clean_steps = []
    for st in raw_steps[:10]:
        if isinstance(st, dict):
            tree = st.get("tree") if isinstance(st.get("tree"), dict) else _tex_to_tree(str(st.get("tex") or st.get("formula") or ""))
            clean_steps.append({"tree": tree, "note": str(st.get("note") or "")[:100]})
        elif isinstance(st, str):
            clean_steps.append({"tree": _tex_to_tree(st), "note": ""})
    out["steps"] = clean_steps


def _sanitize_map_mark(out: dict[str, Any]) -> None:
    pins = out.get("pins") or out.get("places") or []
    clean_pins = [
        {"name": str(p.get("name") or "")[:32], "lon": _safe_float(p.get("lon"), 0.0), "lat": _safe_float(p.get("lat"), 0.0)}
        for p in pins[:12] if isinstance(p, dict) and p.get("name")
    ]
    out["pins"] = clean_pins
    shade_in = out.get("shade") or out.get("countries") or []
    clean_shade = []
    palette = ["teal", "amber", "coral", "sky", "lime", "lilac"]
    for idx, item in enumerate(shade_in[:10]):
        cname = str(item.get("name") if isinstance(item, dict) else item).strip().lower()
        c_idx = _COUNTRY_INDEX.get(cname)
        if c_idx is not None:
            lon = _safe_float(item.get("lon"), 0.0) if isinstance(item, dict) and "lon" in item else 0.0
            lat = _safe_float(item.get("lat"), 0.0) if isinstance(item, dict) and "lat" in item else 0.0
            clean_shade.append({
                "i": c_idx,
                "name": _WORLD_COUNTRIES[c_idx],
                "tone": _resolve_tone(item.get("tone")) if isinstance(item, dict) and item.get("tone") else palette[idx % len(palette)],
                "at": [lon, lat],
            })
    out["shade"] = clean_shade
    view_raw = out.get("view")
    valid_view = False
    if isinstance(view_raw, list) and len(view_raw) == 4:
        try:
            out["view"] = [float(v) for v in view_raw]
            valid_view = True
        except (ValueError, TypeError):
            valid_view = False
    if not valid_view:
        coords = [(p["lon"], p["lat"]) for p in clean_pins] + [
            (s["at"][0], s["at"][1]) for s in clean_shade if s["at"] != [0.0, 0.0]
        ]
        if coords:
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            pad_lon = max(12.0, (max(lons) - min(lons)) * 0.25)
            pad_lat = max(8.0, (max(lats) - min(lats)) * 0.25)
            out["view"] = [min(lons) - pad_lon, min(lats) - pad_lat, max(lons) + pad_lon, max(lats) + pad_lat]
        else:
            out["view"] = [-170.0, -55.0, 175.0, 75.0]


def _sanitize_mindmap_mark(out: dict[str, Any]) -> None:
    out["title"] = str(out.get("title") or "Mind Map")[:60]
    branches = []
    for b in (out.get("branches") or [])[:8]:
        if isinstance(b, dict):
            clean_items = [str(it)[:50] for it in (b.get("items") or [])[:6]]
            branches.append({
                "text": str(b.get("text") or "")[:40],
                "items": clean_items,
                "tone": _resolve_tone(b.get("tone")),
            })
        elif isinstance(b, str):
            branches.append({"text": str(b)[:40], "items": [], "tone": "teal"})
    out["branches"] = branches


def _sanitize_quiz_mark(out: dict[str, Any]) -> None:
    out["part"] = str(out.get("part") or f"quiz_{int(time.time() * 1000)}")
    out["title"] = str(out.get("title") or "Quiz")[:60]
    clean_qs = []
    for q in (out.get("questions") or [])[:12]:
        if isinstance(q, dict):
            opts = [str(o)[:120] for o in (q.get("options") or [])[:6]]
            max_opt = max(0, len(opts) - 1)
            right_idx = _safe_int(q.get("right"), 0, lo=0, hi=max_opt)
            clean_qs.append({
                "text": str(q.get("text") or "")[:240],
                "options": opts,
                "right": right_idx,
                "explain": str(q.get("explain") or "")[:200],
            })
    out["questions"] = clean_qs


def _sanitize_ticket_mark(out: dict[str, Any]) -> None:
    out["part"] = str(out.get("part") or f"ticket_{int(time.time() * 1000)}")
    out["title"] = str(out.get("title") or "Task")[:60]
    out["text"] = str(out.get("text") or "")[:240]
    if "n" in out and out["n"] is not None:
        try:
            out["n"] = int(out["n"])
        except (ValueError, TypeError):
            out["n"] = None
    out["label"] = str(out.get("label") or "Try it")[:32]
    out["action"] = bool(out.get("action", True))


def _sanitize_medal_mark(out: dict[str, Any]) -> None:
    out["part"] = str(out.get("part") or f"medal_{int(time.time() * 1000)}")
    out["title"] = str(out.get("title") or "Goal Achieved")[:60]
    out["text"] = str(out.get("text") or "")[:240]


_MARK_SANITIZERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "model": _sanitize_model_mark,
    "flow": _sanitize_flow_mark,
    "math": _sanitize_math_mark,
    "map": _sanitize_map_mark,
    "mindmap": _sanitize_mindmap_mark,
    "quiz": _sanitize_quiz_mark,
    "ticket": _sanitize_ticket_mark,
    "medal": _sanitize_medal_mark,
}


def _sanitize_mark(mk: dict[str, Any]) -> dict[str, Any]:
    """Normalize mark fields into strict Roamies `ink.js`/`ink3d.js` shapes."""
    out = dict(mk)
    for key in ("kind", "tone"):
        if isinstance(out.get(key), str) and "|" in out[key]:
            out[key] = out[key].split("|", 1)[0].strip().lower()
    if out.get("tone") not in _VALID_TONES:
        out["tone"] = _resolve_tone(out.get("tone"))
    if out.get("kind") == "model3d":
        out["kind"] = "model"

    at_val = out.get("at")
    if isinstance(at_val, dict) and "rect" not in at_val and all(k in at_val for k in ("x", "y")):
        out["at"] = {
            "rect": [
                _safe_float(at_val["x"], 0.0),
                _safe_float(at_val["y"], 0.0),
                _safe_float(at_val.get("w"), 120.0),
                _safe_float(at_val.get("h"), 40.0),
            ]
        }

    kind = str(out.get("kind") or "")
    sanitizer = _MARK_SANITIZERS.get(kind)
    if sanitizer:
        sanitizer(out)

    if kind == "molecule" and "mol" not in out:
        smiles = str(out.get("smiles") or "").strip()
        if smiles:
            out["mol"] = _parse_smiles_to_mol(smiles)

    if "pinned" in out:
        out["pinned"] = bool(out["pinned"])

    return out


def _extract_json_object(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        from amigo.utils.tool_registry import _clean_json_text, _iter_json_objects
        cleaned = _clean_json_text(raw)
        for _, _, obj in _iter_json_objects(cleaned):
            if isinstance(obj, dict):
                return obj
    except Exception as exc:
        logger.debug("[JSON Extract] Primary parser fallback: %s", exc)

    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    m = re.search(r"\{.*", cleaned, re.DOTALL)
    if not m:
        return {}
    candidate = re.sub(r",\s*([\]}])", r"\1", m.group(0))
    open_k = candidate.count("[") - candidate.count("]")
    open_b = candidate.count("{") - candidate.count("}")
    if open_k > 0:
        candidate += "]" * open_k
    if open_b > 0:
        candidate += "}" * open_b
    try:
        loaded = json.loads(candidate)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logger.debug("[JSON Extract] Candidate parse failed: %s", exc)
        last_brace = candidate.rfind("}")
        while last_brace > 0:
            try:
                loaded = json.loads(candidate[: last_brace + 1])
                return loaded if isinstance(loaded, dict) else {}
            except Exception:
                last_brace = candidate.rfind("}", 0, last_brace)
    return {}


def _build_marks_from_parsed(
    parsed: dict[str, Any],
    topic: str,
    vtype: str,
    next_part: Callable[[], str],
) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []
    palette = ["teal", "coral", "amber", "sky", "lilac", "lime"]
    short_topic = topic.title()[:28]

    if isinstance(parsed.get("parts"), list) and parsed["parts"]:
        marks.append(_sanitize_mark({
            "kind": "model",
            "part": next_part(),
            "title": f"{short_topic} · 3D",
            "parts": parsed["parts"],
        }))

    flow_data = parsed.get("flow") if isinstance(parsed.get("flow"), dict) else (
        {"nodes": parsed["nodes"], "links": parsed.get("links", []), "dir": parsed.get("dir", "down")}
        if isinstance(parsed.get("nodes"), list) else None
    )
    if flow_data and isinstance(flow_data.get("nodes"), list) and len(flow_data["nodes"]) >= 2:
        marks.append(_sanitize_mark({
            "kind": "flow",
            "part": next_part(),
            "title": f"{short_topic} · Flow",
            "dir": str(flow_data.get("dir") or "down"),
            "nodes": flow_data["nodes"][:12],
            "links": flow_data.get("links") or [
                {"from": flow_data["nodes"][i]["id"], "to": flow_data["nodes"][i + 1]["id"], "flow": True}
                for i in range(len(flow_data["nodes"]) - 1)
            ],
        }))
    elif isinstance(parsed.get("steps"), list) and len(parsed["steps"]) >= 2 and vtype == "flow":
        raw_steps = parsed["steps"][:8]
        nodes = [
            {
                "id": f"s{i + 1}",
                "text": str(st)[:60],
                "shape": "round" if i in (0, len(raw_steps) - 1) else "box",
                "tone": palette[i % len(palette)],
            }
            for i, st in enumerate(raw_steps)
        ]
        links = [{"from": f"s{i}", "to": f"s{i + 1}", "flow": True} for i in range(1, len(nodes))]
        marks.append(_sanitize_mark({
            "kind": "flow",
            "part": next_part(),
            "title": f"{short_topic} · Flow",
            "nodes": nodes,
            "links": links,
        }))

    graph_data = parsed.get("graph") if isinstance(parsed.get("graph"), dict) else None
    if graph_data and isinstance(graph_data.get("plots"), list) and graph_data["plots"]:
        marks.append(_sanitize_mark({
            "kind": "graph",
            "part": next_part(),
            "title": f"{short_topic} · Graph",
            "x_range": graph_data.get("x_range"),
            "y_range": graph_data.get("y_range"),
            "plots": graph_data["plots"][:4],
            "dots": graph_data.get("dots") or [],
        }))

    math_steps = parsed.get("math_steps") or ([{"tex": parsed["tex"]}] if parsed.get("tex") else [])
    if math_steps:
        marks.append(_sanitize_mark({
            "kind": "math",
            "part": next_part(),
            "title": f"{short_topic} · Math",
            "steps": math_steps,
        }))

    if parsed.get("smiles"):
        marks.append(_sanitize_mark({
            "kind": "molecule",
            "part": next_part(),
            "title": f"{short_topic} · Molecule",
            "smiles": str(parsed["smiles"]),
            "label": topic.title()[:32],
        }))

    geom_data = parsed.get("geometry") if isinstance(parsed.get("geometry"), dict) else None
    if geom_data and isinstance(geom_data.get("vertices"), list) and geom_data["vertices"]:
        marks.append(_sanitize_mark({
            "kind": "geometry",
            "part": next_part(),
            "title": f"{short_topic} · Geometry",
            "vertices": geom_data["vertices"][:14],
            "lines": geom_data.get("lines") or [],
            "angles": geom_data.get("angles") or [],
            "circles": geom_data.get("circles") or [],
            "tone": "teal",
        }))

    map_data = parsed.get("map") if isinstance(parsed.get("map"), dict) else (parsed if vtype == "map" else None)
    if map_data and (map_data.get("pins") or map_data.get("countries") or map_data.get("shade")):
        marks.append(_sanitize_mark({
            "kind": "map",
            "part": next_part(),
            "title": f"{short_topic} · Map",
            "view": map_data.get("view"),
            "countries": map_data.get("countries") or [],
            "shade": map_data.get("shade") or [],
            "pins": map_data.get("pins") or [],
            "routes": map_data.get("routes") or [],
        }))

    bars_data = parsed.get("bars") if isinstance(parsed.get("bars"), list) else (
        parsed.get("data") if vtype == "bars" and isinstance(parsed.get("data"), list) else None
    )
    if bars_data:
        clean_bars = [
            {
                "label": str(b.get("label") or "")[:32],
                "value": _safe_float(b.get("value"), 0.0),
                "tone": _resolve_tone(b.get("tone")) if b.get("tone") else palette[i % len(palette)],
            }
            for i, b in enumerate(bars_data[:12]) if isinstance(b, dict) and b.get("label")
        ]
        if clean_bars:
            marks.append(_sanitize_mark({
                "kind": "bars",
                "part": next_part(),
                "title": f"{short_topic} · Chart",
                "unit": parsed.get("unit"),
                "bars": clean_bars,
            }))

    tl_data = parsed.get("timeline") if isinstance(parsed.get("timeline"), list) else None
    if tl_data and len(tl_data) >= 2:
        events = [
            {"when": str(ev.get("when") or f"{i + 1}")[:20], "text": str(ev.get("text") or "")[:64]}
            for i, ev in enumerate(tl_data[:8]) if isinstance(ev, dict) and ev.get("text")
        ]
        if len(events) >= 2:
            marks.append(_sanitize_mark({
                "kind": "timeline",
                "part": next_part(),
                "title": f"{short_topic} · Timeline",
                "events": events,
            }))

    if isinstance(parsed.get("questions"), list) and parsed["questions"]:
        qs = []
        for q in parsed["questions"][:5]:
            if isinstance(q, dict) and q.get("text") and isinstance(q.get("options"), list):
                opts = [str(o)[:70] for o in q["options"][:4]]
                qs.append({
                    "text": str(q["text"])[:160],
                    "options": opts,
                    "right": _safe_int(q.get("right"), 0, lo=0, hi=max(0, len(opts) - 1)),
                    "explain": str(q.get("explain") or "")[:160],
                })
        if qs:
            marks.append(_sanitize_mark({
                "kind": "quiz",
                "part": next_part(),
                "title": f"{short_topic} · Quiz",
                "questions": qs,
            }))

    tbl_data = parsed.get("table") if isinstance(parsed.get("table"), dict) else None
    if tbl_data and isinstance(tbl_data.get("columns"), list) and isinstance(tbl_data.get("rows"), list) and tbl_data["rows"]:
        cols = [str(c)[:32] for c in tbl_data["columns"][:6]]
        rows = [[str(cell)[:80] for cell in r[: len(cols)]] for r in tbl_data["rows"][:10] if isinstance(r, list)]
        if cols and rows:
            marks.append(_sanitize_mark({
                "kind": "table",
                "part": next_part(),
                "title": str(tbl_data.get("title") or f"{topic.title()[:24]} · Table")[:36],
                "columns": cols,
                "rows": rows,
            }))

    mindmap_data = parsed.get("mindmap") if isinstance(parsed.get("mindmap"), dict) else (parsed if vtype == "mindmap" else None)
    if mindmap_data and (mindmap_data.get("branches") or vtype == "mindmap"):
        marks.append(_sanitize_mark({
            "kind": "mindmap",
            "part": next_part(),
            "title": str(mindmap_data.get("title") or topic.title())[:40],
            "branches": mindmap_data.get("branches") or [],
        }))

    return marks


_HAS_PAYLOAD_KEYS = {
    "flow", "mindmap", "branches", "bars", "graph", "table", "timeline",
    "math_steps", "parts", "questions", "geometry", "smiles", "map", "molecule", "nodes",
}


def _generate_visual_diagram_beats(
    topic: str,
    preferred_kind: Optional[str] = None,
    structured_data: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], str]:
    """Build Roamies `ink.js` / `ink3d.js` visual beats from structured payload or dynamic LLM synthesis."""
    from amigo.core.llm_agent import query_local_llm, sanitize_for_tts

    parsed: dict[str, Any] = dict(structured_data) if isinstance(structured_data, dict) else {}
    if "branches" in parsed and "mindmap" not in parsed:
        parsed["mindmap"] = dict(parsed)

    if not parsed:
        kind_hint = f"Use visual_type='{preferred_kind}'." if preferred_kind else (
            "Choose the single best visual_type from: 'mindmap', 'flow', 'model', 'graph', 'bars', 'table', "
            "'timeline', 'math', 'geometry', 'molecule', 'map', 'quiz', 'card'."
        )
        prompt = (
            f"Create a visual explanation of '{topic}' as compact valid JSON (no markdown).\n"
            f"{kind_hint}\n"
            f"Include 'summary' (1-2 spoken sentences), 'visual_type', and ONLY the matching payload key:\n"
            f"- mindmap: \"mindmap\": {{\"title\":\"...\",\"branches\":[{{\"text\":\"Branch A\",\"items\":[\"point 1\",\"point 2\"],\"tone\":\"teal\"}}]}}\n"
            f"- flow: \"flow\": {{\"dir\":\"down|right\",\"nodes\":[{{\"id\":\"n1\",\"text\":\"...\",\"shape\":\"round|box|diamond\",\"tone\":\"teal|sky|coral|amber\"}}],\"links\":[{{\"from\":\"n1\",\"to\":\"n2\",\"label\":\"optional\",\"flow\":true}}]}}\n"
            f"- model: \"parts\": [{{\"shape\":\"sphere|torus|cone|cylinder|box\",\"label\":\"...\",\"at\":[x,y,z],\"size\":[sx,sy,sz],\"rot\":[rx,ry,rz],\"tone\":\"coral|amber|sky|teal|lilac\",\"motion\":{{\"do\":\"spin|orbit|slide|pulse\",\"axis\":\"y\"}}}}]\n"
            f"- table: \"table\": {{\"title\":\"...\",\"columns\":[...],\"rows\":[[...]]}}\n"
            f"- bars: \"bars\": [{{\"label\":\"...\",\"value\":10}}], \"unit\": \"optional\"\n"
            f"- graph: \"graph\": {{\"plots\":[{{\"label\":\"...\",\"tone\":\"teal\",\"points\":[[x,y],...]}}]}}\n"
            f"- math: \"math_steps\": [{{\"tex\":\"...\",\"note\":\"...\"}}]\n"
            f"- timeline: \"timeline\": [{{\"when\":\"...\",\"text\":\"...\"}}]\n"
            f"- geometry: \"geometry\": {{\"vertices\":[{{\"id\":\"A\",\"x\":0,\"y\":0}}],\"lines\":[{{\"from\":\"A\",\"to\":\"B\",\"label\":\"...\"}}],\"angles\":[...],\"circles\":[...]}}\n"
            f"- map: \"map\": {{\"view\":[west,south,east,north],\"countries\":[{{\"name\":\"...\",\"lon\":0,\"lat\":0}}],\"pins\":[{{\"name\":\"...\",\"lon\":0,\"lat\":0}}],\"routes\":[{{\"from\":\"...\",\"to\":\"...\"}}]}}\n"
            f"- molecule: \"smiles\": \"valid SMILES string\"\n"
            f"- quiz: \"questions\": [{{\"text\":\"...\",\"options\":[...],\"right\":0,\"explain\":\"...\"}}]\n"
            f"Output ONLY valid JSON."
        )
        raw = query_local_llm(prompt, max_tokens=1100, thinking=False)
        parsed = _extract_json_object(raw)

    if not parsed or not any(k in parsed for k in _HAS_PAYLOAD_KEYS):
        pk = preferred_kind or "flow"
        simple_prompt = (
            f"Output ONLY valid compact JSON for a '{pk}' visual of '{topic}'. "
            f"Include summary and the {pk} payload. No markdown, no explanation."
        )
        raw2 = query_local_llm(simple_prompt, max_tokens=600, thinking=False)
        parsed2 = _extract_json_object(raw2)
        if parsed2 and any(k in parsed2 for k in _HAS_PAYLOAD_KEYS):
            parsed = parsed2
        else:
            expl = query_local_llm(
                f"Provide a 1-sentence summary and 3 key points about {topic}.\n"
                f"Format:\nSummary: <one concise sentence>\n- <point 1>\n- <point 2>\n- <point 3>",
                max_tokens=250,
                thinking=False,
            )
            lines = [ln.strip() for ln in expl.splitlines() if ln.strip()]
            lead = ""
            points = []
            for ln in lines:
                if ln.lower().startswith("summary:"):
                    lead = ln[8:].strip()
                elif ln.startswith(("-", "*", "•")) or (len(ln) > 2 and ln[0].isdigit() and ln[1] in (".", ")")):
                    clean_pt = re.sub(r"^[-*•\d.)\s]+", "", ln).strip()
                    if clean_pt:
                        points.append(clean_pt[:90])
                elif not lead:
                    lead = ln
            parsed = {
                "summary": lead or f"Overview of {topic}.",
                "highlights": points[:4] or [f"Key aspects of {topic}."],
            }

    summary = sanitize_for_tts(str(parsed.get("summary") or "")) or f"Here is a visual breakdown of {topic}."
    vtype = str(preferred_kind or parsed.get("visual_type") or "flow").strip().lower()

    part_counter = 1

    def _next_part() -> str:
        nonlocal part_counter
        pid = f"p{part_counter}"
        part_counter += 1
        return pid

    primary_marks = _build_marks_from_parsed(parsed, topic=topic, vtype=vtype, next_part=_next_part)
    beats: list[dict[str, Any]] = [
        {
            "voiced": idx == 0,
            "caption": summary if idx == 0 else f"{topic.title()} — detail {idx + 1}",
            "marks": [mk],
        }
        for idx, mk in enumerate(primary_marks[:3])
    ]

    highlights = [str(h)[:100] for h in (parsed.get("highlights") or [])[:5] if str(h).strip()]
    if not beats or highlights:
        beats.append({
            "voiced": not beats,
            "caption": summary,
            "marks": [_sanitize_mark({
                "kind": "card",
                "part": _next_part(),
                "title": topic.title()[:40],
                "heading": topic.title()[:36],
                "text": summary,
                "items": highlights,
                "tone": "teal",
            })],
        })

    return beats, summary


# ---------------------------------------------------------------------------
# Tool Definitions & Execution Handlers
# ---------------------------------------------------------------------------

PEN_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "screen_pen_annotate",
        "description": (
            "Draws visual explanations, branching flowcharts, 2D graphs, LaTeX math steps, molecules, "
            "world maps, tables, bar charts, timelines, quizzes, 3D models, or live screen UI marks "
            "(circle, ring, arrow, box, underline, highlight, spotlight) on the Roamies desktop overlay."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "diagram", "flow", "graph", "bars", "table", "timeline",
                        "math", "geometry", "molecule", "map", "quiz", "model",
                        "card", "note", "list", "circle", "ring", "arrow", "box",
                        "underline", "highlight", "spotlight", "clear",
                    ],
                    "description": (
                        "Visual type to draw: 'flow' for flowcharts/processes, 'model' for 3D models, "
                        "'graph'/'bars'/'table'/'timeline'/'math'/'geometry'/'molecule'/'map'/'quiz'/'card' "
                        "for visual explanations, or 'circle'/'ring'/'arrow'/'box'/'underline'/'highlight'/'spotlight' "
                        "to mark live on-screen UI elements."
                    ),
                },
                "topic": {
                    "type": "string",
                    "description": "Concept, system, or process to visually explain.",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Optional structured diagram data matching the chosen action (e.g. "
                        "{nodes:[{id,text,shape,tone}], links:[{from,to,label}]} for 'flow', "
                        "{parts:[...]} for 'model', {columns:[...], rows:[...]} for 'table', etc.)."
                    ),
                },
                "x": {"type": "number", "description": "Target X coordinate for screen marks (0-1000 normalized by default)."},
                "y": {"type": "number", "description": "Target Y coordinate for screen marks (0-1000 normalized by default)."},
                "x2": {"type": "number", "description": "Optional secondary X coordinate for arrows/boxes/underlines."},
                "y2": {"type": "number", "description": "Optional secondary Y coordinate for arrows/boxes."},
                "label": {"type": "string", "description": "On-screen UI element text to locate via OCR/UIA, or label chip text."},
                "color": {"type": "string", "description": "Palette tone ('teal', 'coral', 'amber', 'sky', 'lilac', 'lime')."},
                "duration": {"type": "number", "description": "Seconds before auto-fading."},
                "normalized": {"type": "boolean", "description": "Whether x/y are 0-1000 normalized (default true)."},
            },
            "required": ["action"],
        },
    },
}

GUIDE_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "screen_pen_guide",
        "description": (
            "Draws a step-by-step interactive on-screen walkthrough highlighting live UI controls "
            "with numbered rings and captions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string", "description": "Visible UI button/menu/field text to locate on screen."},
                            "caption": {"type": "string", "description": "Instructional caption for this step."},
                        },
                        "required": ["target"],
                    },
                    "description": "Ordered list of UI targets and instructions.",
                },
                "topic": {"type": "string", "description": "Title of the walkthrough."},
                "app": {"type": "string", "description": "Optional target application name to scope UI search."},
                "duration": {"type": "number", "description": "Seconds before auto-fading."},
            },
            "required": ["steps"],
        },
    },
}


def _emit_ink_with_sfx(event: Dict[str, Any], sfx_name: str = "ink") -> int:
    emit_overlay_sfx(sfx_name)
    return push_overlay_event(event)


def screen_pen_annotate(
    action: str,
    x: Optional[int | float] = None,
    y: Optional[int | float] = None,
    x2: Optional[int | float] = None,
    y2: Optional[int | float] = None,
    label: Optional[str] = None,
    color: str = "teal",
    duration: float = 12.0,
    normalized: bool = True,
    topic: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
    beats: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Emit a Roamies JavaScript `ink.js` / `ink3d.js` beat event for screen annotations or visual diagrams."""
    act = str(action or "").strip().lower()
    if act == "clear":
        event: Dict[str, Any] = {"type": "ink_clear", "all": True}
        subscribers = push_overlay_event(event)
        return {"status": "success", "drawn": "clear", "engine": "roamies-ink-js", "subscribers": subscribers, "event": event}

    if act in {"erase", "recolor", "move"}:
        target_part = str((data or {}).get("part") or label or "p1")
        dx = _safe_float((data or {}).get("dx"), 0.0)
        dy = _safe_float((data or {}).get("dy"), 0.0)
        subscribers = emit_overlay_edit(action=act, part=target_part, tone=color, dx=dx, dy=dy)
        return {
            "status": "success",
            "action": act,
            "part": target_part,
            "engine": "roamies-ink-js",
            "subscribers": subscribers,
        }

    if act == "pin":
        target_part = str((data or {}).get("part") or label or "p1")
        is_on = bool((data or {}).get("on", True))
        subscribers = emit_pin_part(target_part, on=is_on)
        return {
            "status": "success",
            "action": "pin",
            "part": target_part,
            "on": is_on,
            "subscribers": subscribers,
        }

    if label and not (data or beats):
        if act not in _SCREEN_MARK_KINDS:
            act = "circle"
    elif act not in (_DIAGRAM_KINDS | _SCREEN_MARK_KINDS):
        act = "diagram" if (topic or data or beats) else "circle"

    if act in _DIAGRAM_KINDS:
        diag_topic = str(topic or label or "Visual Explanation").strip()
        preferred = None if act == "diagram" else act
        if isinstance(beats, list) and beats:
            diag_beats = [
                {
                    "caption": b.get("caption", ""),
                    "voiced": b.get("voiced", True),
                    "marks": [_sanitize_mark(m) for m in (b.get("marks") or []) if isinstance(m, dict)],
                }
                for b in beats if isinstance(b, dict)
            ]
            summary = f"Drew visual explanation for {diag_topic}."
        else:
            diag_beats, summary = _generate_visual_diagram_beats(diag_topic, preferred_kind=preferred, structured_data=data)

        event = {
            "type": "ink",
            "id": f"ink-{int(time.time() * 1000)}",
            "seg": 0,
            "last": True,
            "fade": max(24.0, _safe_float(duration, 12.0)),
            "beats": diag_beats,
        }
        subscribers = _emit_ink_with_sfx(event, "ink")
        return {
            "status": "success",
            "drawn": act,
            "engine": "roamies-ink-js",
            "topic": diag_topic,
            "summary": summary,
            "subscribers": subscribers,
            "event": event,
        }

    has_explicit_coords = x is not None and y is not None
    resolved_rect = _resolve_target_rect(label) if (label and not has_explicit_coords) else None
    if label and not has_explicit_coords and not resolved_rect:
        return {
            "status": "error",
            "code": "TARGET_NOT_FOUND",
            "error": f"Could not locate '{label}' on screen.",
        }

    sw, sh = _get_viewport_bounds()
    default_x = 500.0 if normalized else sw * 0.5
    default_y = 500.0 if normalized else sh * 0.5
    px, py = _to_screen_points(x if x is not None else default_x, y if y is not None else default_y, normalized)
    tone = _resolve_tone(color)

    default_span_x = round(sw * 0.06, 1)
    default_span_y = round(sh * 0.05, 1)

    if act == "arrow":
        if resolved_rect:
            rx, ry, rw, rh = resolved_rect
            tx, ty = rx + rw / 2.0, ry + rh / 2.0
            offset_x = -default_span_x if tx > sw * 0.75 else default_span_x
            offset_y = default_span_y if ty < sh * 0.25 else -default_span_y
            mark: Dict[str, Any] = {"kind": "arrow", "points": [[tx + offset_x, ty + offset_y], [tx, ty]], "tone": tone, "flow": True}
        else:
            raw_x = _safe_float(x, default_x)
            raw_y = _safe_float(y, default_y)
            px2, py2 = _to_screen_points(
                x2 if x2 is not None else raw_x + (80.0 if normalized else default_span_x),
                y2 if y2 is not None else raw_y - (60.0 if normalized else default_span_y),
                normalized,
            )
            mark = {"kind": "arrow", "points": [[px2, py2], [px, py]], "tone": tone, "flow": True}
    elif act in {"rect", "box"}:
        if resolved_rect:
            mark = {"kind": "rect", "at": {"rect": resolved_rect}, "tone": tone}
        else:
            raw_x = _safe_float(x, default_x)
            raw_y = _safe_float(y, default_y)
            px2, py2 = _to_screen_points(
                x2 if x2 is not None else raw_x + (80.0 if normalized else default_span_x),
                y2 if y2 is not None else raw_y + (50.0 if normalized else default_span_y),
                normalized,
            )
            bx, by = min(px, px2), min(py, py2)
            bw, bh = max(24.0, abs(px2 - px)), max(24.0, abs(py2 - py))
            mark = {"kind": "rect", "at": {"rect": [bx, by, bw, bh]}, "tone": tone}
    elif act == "underline":
        if resolved_rect:
            mark = {"kind": "underline", "at": {"rect": resolved_rect}, "tone": tone}
        else:
            w = max(32.0, abs(_to_screen_points(x2, y if y is not None else default_y, normalized)[0] - px)) if x2 is not None else default_span_x * 1.5
            mark = {"kind": "underline", "at": {"rect": [px - w / 2.0, py - 10.0, w, 20.0]}, "tone": tone}
    elif act in {"highlight", "spotlight", "ring", "point", "target", "hover"}:
        rect = resolved_rect or [px - default_span_x / 2.0, py - default_span_y / 2.0, default_span_x, default_span_y]
        mark = {"kind": act, "at": {"rect": rect}, "tone": tone}
    else:
        r_size = min(default_span_x, default_span_y)
        rect = resolved_rect or [px - r_size / 2.0, py - r_size / 2.0, r_size, r_size]
        mark = {"kind": "circle", "at": {"rect": rect}, "tone": tone}

    if label:
        mark["label"] = str(label)[:40]
    mark["part"] = "p1"

    event = {
        "type": "ink",
        "id": f"ink-{int(time.time() * 1000)}",
        "seg": 0,
        "last": True,
        "fade": max(4.0, _safe_float(duration, 12.0)),
        "beats": [{"voiced": True, "caption": label or "", "marks": [mark]}],
    }
    subscribers = _emit_ink_with_sfx(event, "ink")
    return {
        "status": "success",
        "drawn": act,
        "engine": "roamies-ink-js",
        "resolved_rect": resolved_rect,
        "subscribers": subscribers,
        "event": event,
    }


def screen_pen_guide(
    steps: Any,
    topic: str | None = None,
    app: str | None = None,
    duration: float = 18.0,
) -> Dict[str, Any]:
    """Draw a multi-step Roamies visual walkthrough (`ring` + `caption`) over live UI targets."""
    parsed_steps: list[Any] = []
    if isinstance(steps, str):
        steps_str = steps.strip()
        if steps_str.startswith("[") and steps_str.endswith("]"):
            try:
                candidate = json.loads(steps_str)
                if isinstance(candidate, list):
                    parsed_steps = candidate
            except Exception as exc:
                logger.debug("[Screen Guide] Failed parsing JSON steps: %s", exc)
        if not parsed_steps:
            for line in re.split(r"\r?\n+", steps_str):
                cleaned = re.sub(r"^(?:\d+[\.\)]|\-|\*)\s*", "", line).strip()
                if cleaned:
                    parsed_steps.append(cleaned)
    elif isinstance(steps, list):
        parsed_steps = list(steps)

    if not parsed_steps:
        return {"status": "error", "code": "NO_STEPS", "error": "steps must be provided as a non-empty list."}

    palette = ["teal", "amber", "sky", "coral"]
    beats: list[dict[str, Any]] = []
    resolved_count = 0

    for idx, raw_step in enumerate(parsed_steps[:8]):
        target = ""
        caption = ""
        if isinstance(raw_step, str):
            text_step = raw_step.strip()
            if ":" in text_step and len(text_step.split(":", 1)[0].split()) <= 4:
                target, caption = [s.strip() for s in text_step.split(":", 1)]
                caption = caption or f"Step {idx + 1}: {target}"
            else:
                target = text_step
                caption = f"Step {idx + 1}: {target}"
        elif isinstance(raw_step, dict):
            target = str(raw_step.get("target") or raw_step.get("label") or raw_step.get("name") or "").strip()
            caption = str(raw_step.get("caption") or raw_step.get("text") or f"Step {idx + 1}: {target}").strip()
            if not target and caption:
                target = caption[:32]
        else:
            continue

        rect = _resolve_target_rect(target, app=app) if target else None
        tone = palette[idx % len(palette)]
        marks: list[dict[str, Any]] = []
        if rect:
            resolved_count += 1
            marks.append({
                "kind": "ring",
                "part": f"step{idx + 1}",
                "at": {"rect": rect},
                "label": f"{idx + 1}. {target}"[:36],
                "tone": tone,
            })
        else:
            marks.append({
                "kind": "card",
                "part": f"step{idx + 1}",
                "heading": f"Step {idx + 1}",
                "title": f"{topic or 'Guide'} · {idx + 1}"[:32],
                "text": caption[:150],
                "tone": tone,
            })
        beats.append({
            "voiced": True,
            "marks": marks,
            "caption": caption[:150],
        })

    if not beats:
        return {"status": "error", "code": "NO_VALID_STEPS", "error": "No valid guide steps."}

    event = {
        "type": "ink",
        "id": f"guide-{int(time.time() * 1000)}",
        "seg": 0,
        "last": True,
        "fade": max(8.0, _safe_float(duration, 18.0)),
        "beats": beats,
    }
    subscribers = _emit_ink_with_sfx(event, "ink")
    return {
        "status": "success",
        "drawn": "guide",
        "engine": "roamies-ink-js",
        "step_count": len(beats),
        "resolved_targets": resolved_count,
        "subscribers": subscribers,
        "event": event,
    }