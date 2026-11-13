"""
Screen Pen & Visual Overlay Bridge for Amigo Voice Assistant.
Drives the JavaScript-native Roamies overlay (`roamies-win-extract/overlay/desktop.html` + `ink.js` + `ink3d.js`)
via SSE events with full support for branching flowcharts, 2D graphs, LaTeX math trees, molecules,
maps, quizzes, tables, timelines, bar charts, geometry, and 3D models.
"""

from __future__ import annotations

import json
import math
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

_VALID_TONES = {"teal", "amber", "coral", "lilac", "sky", "lime", "red", "white", "accent"}
_VALID_3D_SHAPES = {"sphere", "torus", "cone", "cylinder", "box"}

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
_COUNTRY_CENTERS: dict[str, tuple[float, float]] = {
    "india": (78.9, 21.0), "united states": (-98.5, 39.8), "united kingdom": (-2.0, 54.0),
    "china": (104.1, 35.8), "japan": (138.2, 36.2), "germany": (10.4, 51.1), "france": (2.2, 46.2),
    "brazil": (-51.9, -14.2), "australia": (133.7, -25.2), "canada": (-106.3, 56.1),
    "russia": (105.3, 61.5), "egypt": (30.8, 26.8), "south africa": (22.9, -30.5),
    "italy": (12.5, 41.8), "spain": (-3.7, 40.4), "mexico": (-102.5, 23.6),
    "united arab emirates": (53.8, 23.4), "saudi arabia": (45.0, 23.8), "singapore": (103.8, 1.35),
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
    """Broadcast a Roamies overlay event (`ink`, `ink_clear`, `sfx`, etc.) to connected JS overlays."""
    global _LAST_EVENT
    with _SUBSCRIBERS_LOCK:
        _LAST_EVENT = (time.time(), event) if event.get("type") not in ("ink_clear", "sfx") else _LAST_EVENT
        if event.get("type") == "ink_clear":
            _LAST_EVENT = None
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
    from amigo.services.hands import get_screen_bounds
    sw, sh = get_screen_bounds()
    return round((float(x) / 1000.0) * sw, 1), round((float(y) / 1000.0) * sh, 1)


def play_overlay_sfx(name: str = "ink") -> int:
    """Trigger a Roamies WebAudio `sfx.js` sound effect on connected overlays."""
    return push_overlay_event({"type": "sfx", "name": str(name or "ink").strip()})


def _resolve_target_rect(label: str, app: str | None = None) -> list[float] | None:
    """Resolve real on-screen bounds `[x, y, w, h]` for *label* via Rust `hands` (`front` -> `screen`)."""
    targets = [f"app:{app}"] if app else ["front", "screen"]
    try:
        from amigo.services import hands
        for tgt in targets:
            hit = hands.find_text(tgt, str(label), scroll=False)
            matches = hit.get("matches") or []
            if not matches:
                hit = hands.ocr_find(tgt, str(label))
                matches = hit.get("matches") or []
            if matches and isinstance(matches[0], dict) and isinstance(matches[0].get("bounds"), list):
                b = matches[0]["bounds"]
                if len(b) == 4:
                    return [float(b[0]), float(b[1]), max(24.0, float(b[2])), max(24.0, float(b[3]))]
    except Exception:
        pass
    return None


def _color_to_tone(color: str) -> str:
    c = str(color or "").strip().lower()
    if c in _VALID_TONES:
        return c
    return _COLOR_TONES.get(c.upper(), "teal")


PEN_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "screen_pen_annotate",
        "description": (
            "Draws visual explanations, interactive diagrams, flowcharts, 2D graphs, LaTeX math steps, "
            "molecules, world maps, tables, bar charts, quizzes, 3D models, or screen UI annotations "
            "(circle, arrow, box, underline, spotlight) on the desktop overlay."
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
                        "underline", "highlight", "spotlight", "guide", "clear",
                    ],
                    "description": (
                        "Use 'diagram' (or specific visual types like 'flow', 'graph', 'math', 'molecule', 'map', "
                        "'table', 'bars', 'timeline', 'quiz', 'model') to visually explain any topic, or use "
                        "'circle'/'ring'/'arrow'/'box'/'underline'/'spotlight'/'guide' to mark live screen UI elements."
                    ),
                },
                "topic": {
                    "type": "string",
                    "description": "The concept, question, system, or process to visually explain.",
                },
                "x": {"type": "integer", "description": "Target X coordinate for screen annotations."},
                "y": {"type": "integer", "description": "Target Y coordinate for screen annotations."},
                "x2": {"type": "integer", "description": "Optional secondary X coordinate."},
                "y2": {"type": "integer", "description": "Optional secondary Y coordinate."},
                "label": {"type": "string", "description": "UI element text to locate on screen, or label for the mark."},
                "color": {"type": "string", "description": "Color or tone ('teal', 'coral', 'amber', 'sky', 'lilac', 'lime')."},
                "duration": {"type": "number", "description": "Duration in seconds before auto-fading."},
                "normalized": {"type": "boolean", "description": "Whether x/y are 0-1000 normalized (default true)."},
            },
            "required": ["action"],
        },
    },
}


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

    via: dict[int, list[str]] = {}
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
            via[k] = lanes

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
                matched_fn = None
                for fn in _FN_NAMES:
                    if text.startswith(fn, i) and (i + len(fn) == n or not text[i + len(fn)].isalpha()):
                        matched_fn = fn
                        break
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
        if len(children) == 1:
            return children[0]
        return {"t": "row", "c": children}

    return _parse(s)


_VALENCE = {"C": 4, "N": 3, "O": 2, "S": 2, "P": 3, "F": 1, "Cl": 1, "Br": 1, "I": 1}


def _parse_smiles_to_mol(smiles: str) -> dict[str, Any]:
    """Parse a SMILES string into `{atoms: [{el, h, charge}], bonds: [{a, b, order}]}` for `ink.js` `drawMolecule`."""
    atoms: list[dict[str, Any]] = []
    bonds: list[dict[str, Any]] = []
    stack: list[int] = []
    rings: dict[str, tuple[int, float]] = {}
    prev: Optional[int] = None
    next_order: float = 1.0
    i = 0
    s = str(smiles or "CCO").strip()

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
        elif ch.isdigit():
            if prev is not None:
                if ch in rings:
                    r_atom, r_ord = rings.pop(ch)
                    if r_atom != prev:
                        bonds.append({"a": r_atom, "b": prev, "order": r_ord})
                else:
                    rings[ch] = (prev, next_order)
            next_order = 1.0
            i += 1
        elif ch.isalpha():
            arom = ch.islower()
            el = ch.upper()
            if i + 1 < len(s) and s[i : i + 2] in ("Cl", "Br"):
                el = s[i : i + 2]
                i += 2
            else:
                i += 1
            idx = len(atoms)
            atoms.append({"el": el, "h": 0, "charge": 0, "_arom": arom})
            if prev is not None:
                order = 1.5 if (arom and atoms[prev].get("_arom")) else next_order
                bonds.append({"a": prev, "b": idx, "order": order})
            prev = idx
            next_order = 1.0
        else:
            i += 1

    for idx, a in enumerate(atoms):
        a.pop("_arom", None)
        if a["el"] != "C" and a["el"] in _VALENCE:
            used = sum(int(round(b["order"])) for b in bonds if b["a"] == idx or b["b"] == idx)
            a["h"] = max(0, _VALENCE[a["el"]] - used)

    if not atoms:
        return {"atoms": [{"el": "O", "h": 2, "charge": 0}], "bonds": []}
    return {"atoms": atoms, "bonds": bonds}


# ---------------------------------------------------------------------------
# Normalization & Multi-Modal Visual Beat Generation
# ---------------------------------------------------------------------------

def _vec3(val: Any, default: list[float], lo: float, hi: float) -> list[float]:
    if isinstance(val, (list, tuple)) and len(val) == 3:
        try:
            return [max(lo, min(hi, float(v))) for v in val]
        except (TypeError, ValueError):
            pass
    return list(default)


def _sanitize_mark(mk: dict[str, Any]) -> dict[str, Any]:
    """Normalize LLM-generated mark fields into strict Roamies `ink.js`/`ink3d.js` shapes."""
    out = dict(mk)
    for key in ("kind", "tone"):
        if isinstance(out.get(key), str) and "|" in out[key]:
            out[key] = out[key].split("|", 1)[0].strip().lower()
    if out.get("tone") not in _VALID_TONES:
        out["tone"] = "teal"
    if out.get("kind") == "model3d":
        out["kind"] = "model"

    at_val = out.get("at")
    if isinstance(at_val, dict) and "rect" not in at_val and all(k in at_val for k in ("x", "y")):
        out["at"] = {"rect": [float(at_val["x"]), float(at_val["y"]), float(at_val.get("w", 160)), float(at_val.get("h", 160))]}

    if isinstance(out.get("parts"), list):
        offsets = [(0.0, 0.9, 0.0), (0.0, 0.0, 0.0), (0.0, -0.9, 0.0), (1.0, 0.2, 0.0), (-1.0, 0.2, 0.0), (0.0, 0.4, 0.9)]
        tones_cycle = ["coral", "amber", "sky", "teal", "lilac", "lime"]
        clean_parts = []
        for idx, p in enumerate(out["parts"][:6]):
            if not isinstance(p, dict):
                continue
            cp = dict(p)
            shape = str(cp.get("shape") or "sphere").split("|", 1)[0].strip().lower()
            cp["shape"] = shape if shape in _VALID_3D_SHAPES else "sphere"
            tone = str(cp.get("tone") or tones_cycle[idx % len(tones_cycle)]).split("|", 1)[0].strip().lower()
            cp["tone"] = tone if tone in _VALID_TONES else "teal"
            cp["at"] = _vec3(cp.pop("pos", None) if "at" not in cp else cp.get("at"), list(offsets[idx % len(offsets)]), -2.5, 2.5)
            cp["size"] = _vec3(cp.get("size"), [0.65, 0.65, 0.65], 0.15, 2.5)
            if isinstance(cp.get("rot"), (list, tuple)) and len(cp["rot"]) == 3:
                cp["rot"] = _vec3(cp["rot"], [0.0, 0.0, 0.0], -360.0, 360.0)
            if isinstance(cp.get("motion"), str):
                cp["motion"] = {"do": cp["motion"].split("|", 1)[0].strip()}
            clean_parts.append(cp)
        out["parts"] = clean_parts

    if out.get("kind") == "flow" and isinstance(out.get("nodes"), list):
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
                "text": str(n.get("text") or nid)[:64],
                "shape": shape,
                "tone": _color_to_tone(str(n.get("tone") or "teal")),
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
                    "label": str(lk.get("label") or "")[:28] if lk.get("label") else None,
                    "flow": bool(lk.get("flow", True)),
                })
        out["nodes"] = clean_nodes
        out["links"] = clean_links
        out["lay"] = _build_flow_layout(clean_nodes, clean_links)

    if out.get("kind") == "math":
        raw_steps = out.get("steps") or []
        if not raw_steps and out.get("tex"):
            raw_steps = [{"tex": str(out["tex"]), "note": ""}]
        clean_steps = []
        for st in raw_steps[:8]:
            if isinstance(st, dict):
                tree = st.get("tree") if isinstance(st.get("tree"), dict) else _tex_to_tree(str(st.get("tex") or st.get("formula") or ""))
                clean_steps.append({"tree": tree, "note": str(st.get("note") or "")[:90]})
            elif isinstance(st, str):
                clean_steps.append({"tree": _tex_to_tree(st), "note": ""})
        out["steps"] = clean_steps

    if out.get("kind") == "map":
        pins = out.get("pins") or out.get("places") or []
        clean_pins = [
            {"name": str(p.get("name") or "")[:28], "lon": float(p.get("lon") or 0), "lat": float(p.get("lat") or 0)}
            for p in pins[:10] if isinstance(p, dict) and p.get("name")
        ]
        out["pins"] = clean_pins
        shade_in = out.get("shade") or out.get("countries") or []
        clean_shade = []
        tones_cycle = ["teal", "amber", "coral", "sky", "lime", "lilac"]
        for idx, item in enumerate(shade_in[:8]):
            cname = str(item.get("name") if isinstance(item, dict) else item).strip().lower()
            c_idx = _COUNTRY_INDEX.get(cname)
            if c_idx is not None:
                center = _COUNTRY_CENTERS.get(cname, (0.0, 20.0))
                clean_shade.append({
                    "i": c_idx,
                    "name": _WORLD_COUNTRIES[c_idx],
                    "tone": tones_cycle[idx % len(tones_cycle)],
                    "at": [center[0], center[1]],
                })
        out["shade"] = clean_shade
        if not isinstance(out.get("view"), list) or len(out["view"]) != 4:
            if clean_pins:
                lons = [p["lon"] for p in clean_pins]
                lats = [p["lat"] for p in clean_pins]
                out["view"] = [min(lons) - 18, min(lats) - 12, max(lons) + 18, max(lats) + 12]
            else:
                out["view"] = [-25.0, -35.0, 145.0, 65.0]

    if out.get("kind") == "molecule" and "mol" not in out:
        out["mol"] = _parse_smiles_to_mol(str(out.get("smiles") or "CCO"))

    return out


def _generate_visual_diagram_beats(topic: str, preferred_kind: Optional[str] = None) -> tuple[list[dict[str, Any]], str]:
    """
    Generate rich, multi-card Roamies `ink.js` / `ink3d.js` visual beats dynamically for *topic*.
    Supports 3D models, branching/cyclic flowcharts, 2D graphs, LaTeX math ASTs, SMILES molecules,
    world maps, geometry, bar charts, timelines, tables, and interactive quizzes.
    """
    from amigo.core.llm_agent import query_local_llm, sanitize_for_tts

    tones = ["teal", "coral", "amber", "sky", "lilac", "lime"]

    prompt = (
        f"Explain '{topic}' visually as compact valid JSON (no markdown, no comments).\n"
        f"Pick 'visual_type' from: 'flow', 'model', 'graph', 'bars', 'table', 'timeline', 'math', 'geometry', 'molecule', 'map', 'quiz'.\n"
        f"Required keys:\n"
        f"- \"summary\": \"1-2 short spoken sentences\"\n"
        f"- \"visual_type\": \"{preferred_kind or 'flow'}\"\n"
        f"- \"flow\": {{\"nodes\":[{{\"id\":\"n1\",\"text\":\"Short step\",\"shape\":\"round|box|diamond\",\"tone\":\"teal|sky|coral|amber\"}}],\"links\":[{{\"from\":\"n1\",\"to\":\"n2\",\"flow\":true}}]}} (4-6 connected nodes)\n"
        f"- \"parts\": 4-5 3D parts [{{\"shape\":\"sphere|torus|cone|cylinder|box\",\"label\":\"1-3 words\",\"at\":[x,y,z],\"size\":[sx,sy,sz],\"rot\":[rx,ry,rz],\"tone\":\"coral|amber|sky|teal|lilac\",\"glass\":false,\"motion\":{{\"do\":\"spin|orbit|slide|pulse\",\"axis\":\"y\"}}}}] (spread 'at' across -1.4..1.4)\n"
        f"- Include ONLY if relevant to visual_type: \"table\" ({{\"columns\":[...],\"rows\":[[...]]}}), \"bars\" ([{{\"label\":\"...\",\"value\":10}}]), \"graph\" ({{\"plots\":[{{\"label\":\"...\",\"points\":[[0,0],[1,1]]}}]}}), \"math_steps\" ([{{\"tex\":\"...\",\"note\":\"...\"}}]), \"timeline\" ([{{\"when\":\"...\",\"text\":\"...\"}}]), \"geometry\" ({{\"vertices\":[...],\"lines\":[...]}}), \"map\" ({{\"countries\":[...],\"pins\":[...]}}), \"smiles\" (string), or \"questions\" (quiz).\n"
        f"Output ONLY valid JSON."
    )

    raw = query_local_llm(prompt, max_tokens=900, thinking=False)
    summary = f"Here is a visual breakdown of {topic}."
    beats: list[dict[str, Any]] = []
    part_counter = 1

    def _next_part() -> str:
        nonlocal part_counter
        pid = f"p{part_counter}"
        part_counter += 1
        return pid

    parsed: dict[str, Any] = {}
    if raw:
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
        m = re.search(r"\{.*", cleaned, re.DOTALL)
        if m:
            candidate = re.sub(r",\s*([\]}])", r"\1", m.group(0))
            open_k = candidate.count("[") - candidate.count("]")
            open_b = candidate.count("{") - candidate.count("}")
            if open_k > 0:
                candidate += "]" * open_k
            if open_b > 0:
                candidate += "}" * open_b
            try:
                parsed = json.loads(candidate)
            except Exception:
                # Try trimming after the last complete '}'
                last_brace = candidate.rfind("}")
                while last_brace > 0:
                    try:
                        parsed = json.loads(candidate[: last_brace + 1])
                        break
                    except Exception:
                        last_brace = candidate.rfind("}", 0, last_brace)

    if parsed.get("summary"):
        summary = sanitize_for_tts(str(parsed["summary"])) or summary

    vtype = str(preferred_kind or parsed.get("visual_type") or "flow").strip().lower()
    primary_marks: list[dict[str, Any]] = []

    # 3D Model (`drawModel` / `ink3d.js`)
    raw_parts = parsed.get("parts") if isinstance(parsed.get("parts"), list) else None
    if raw_parts:
        primary_marks.append(_sanitize_mark({
            "kind": "model",
            "part": _next_part(),
            "title": f"{topic.title()[:26]} · 3D",
            "parts": raw_parts,
        }))

    # Branching / Cyclic Flowchart (`drawFlow`)
    flow_data = parsed.get("flow") if isinstance(parsed.get("flow"), dict) else None
    if flow_data and isinstance(flow_data.get("nodes"), list) and len(flow_data["nodes"]) >= 2:
        primary_marks.append(_sanitize_mark({
            "kind": "flow",
            "part": _next_part(),
            "title": f"{topic.title()[:28]} · Pathway",
            "dir": str(flow_data.get("dir") or "down"),
            "nodes": flow_data["nodes"][:9],
            "links": flow_data.get("links") or [
                {"from": flow_data["nodes"][i]["id"], "to": flow_data["nodes"][i + 1]["id"], "flow": True}
                for i in range(len(flow_data["nodes"]) - 1)
            ],
        }))
    elif isinstance(parsed.get("steps"), list) and len(parsed["steps"]) >= 2:
        raw_steps = parsed["steps"][:7]
        nodes = [
            {"id": f"s{i + 1}", "text": str(st)[:54], "shape": "round" if i in (0, len(raw_steps) - 1) else "box", "tone": tones[i % len(tones)]}
            for i, st in enumerate(raw_steps)
        ]
        links = [{"from": f"s{i}", "to": f"s{i + 1}", "flow": True} for i in range(1, len(nodes))]
        primary_marks.append(_sanitize_mark({
            "kind": "flow",
            "part": _next_part(),
            "title": f"{topic.title()[:28]} · Flow",
            "nodes": nodes,
            "links": links,
        }))

    # 2D Line/Curve Graph (`drawGraph`)
    graph_data = parsed.get("graph") if isinstance(parsed.get("graph"), dict) else None
    if vtype == "graph" or (graph_data and isinstance(graph_data.get("plots"), list) and graph_data["plots"]):
        plots = (graph_data or {}).get("plots") or [
            {"label": topic.title()[:20], "tone": "teal", "points": [[x, round(math.sin(x * 0.6) * 40 + 50, 1)] for x in range(0, 11)]}
        ]
        primary_marks.append(_sanitize_mark({
            "kind": "graph",
            "part": _next_part(),
            "title": f"{topic.title()[:28]} · Graph",
            "x_range": (graph_data or {}).get("x_range"),
            "y_range": (graph_data or {}).get("y_range"),
            "plots": plots[:4],
            "dots": (graph_data or {}).get("dots") or [],
        }))

    # Math LaTeX AST (`drawMath`)
    math_steps = parsed.get("math_steps") or ([{"tex": parsed["tex"]}] if parsed.get("tex") else [])
    if (vtype == "math" or math_steps) and math_steps:
        primary_marks.append(_sanitize_mark({
            "kind": "math",
            "part": _next_part(),
            "title": f"{topic.title()[:28]} · Equations",
            "steps": math_steps,
        }))

    # Chemistry Molecule (`drawMolecule`)
    smiles_str = parsed.get("smiles")
    if vtype == "molecule" or smiles_str:
        primary_marks.append(_sanitize_mark({
            "kind": "molecule",
            "part": _next_part(),
            "title": f"{topic.title()[:28]} · Structure",
            "smiles": str(smiles_str or "c1ccccc1"),
            "label": topic.title()[:32],
        }))

    # Geometry (`drawGeometry`)
    geom_data = parsed.get("geometry") if isinstance(parsed.get("geometry"), dict) else None
    if (vtype == "geometry" or geom_data) and isinstance((geom_data or parsed).get("vertices"), list):
        src_g = geom_data or parsed
        if src_g.get("vertices"):
            primary_marks.append(_sanitize_mark({
                "kind": "geometry",
                "part": _next_part(),
                "title": f"{topic.title()[:28]} · Geometry",
                "vertices": src_g["vertices"][:12],
                "lines": src_g.get("lines") or [],
                "angles": src_g.get("angles") or [],
                "circles": src_g.get("circles") or [],
                "tone": "teal",
            }))

    # World Map (`drawMap`)
    map_data = parsed.get("map") if isinstance(parsed.get("map"), dict) else None
    if vtype == "map" or (map_data and (map_data.get("pins") or map_data.get("countries"))):
        src_m = map_data or parsed
        primary_marks.append(_sanitize_mark({
            "kind": "map",
            "part": _next_part(),
            "title": f"{topic.title()[:28]} · Map",
            "view": src_m.get("view"),
            "countries": src_m.get("countries") or [],
            "pins": src_m.get("pins") or src_m.get("places") or [],
            "routes": src_m.get("routes") or [],
        }))

    # Bar Chart (`drawBars`)
    bars_data = parsed.get("bars") if isinstance(parsed.get("bars"), list) else None
    if bars_data:
        clean_bars = [
            {"label": str(b.get("label") or "")[:28], "value": float(b.get("value") or 0), "tone": tones[i % len(tones)]}
            for i, b in enumerate(bars_data[:10]) if isinstance(b, dict) and b.get("label")
        ]
        if clean_bars:
            primary_marks.append(_sanitize_mark({
                "kind": "bars",
                "part": _next_part(),
                "title": f"{topic.title()[:28]} · Metrics",
                "bars": clean_bars,
            }))

    # Timeline (`timeline`)
    tl_data = parsed.get("timeline") if isinstance(parsed.get("timeline"), list) else None
    if vtype == "timeline" and tl_data and len(tl_data) >= 2:
        events = [
            {"when": str(ev.get("when") or f"Step {i + 1}")[:18], "text": str(ev.get("text") or "")[:56]}
            for i, ev in enumerate(tl_data[:8]) if isinstance(ev, dict) and ev.get("text")
        ]
        if len(events) >= 2:
            primary_marks.append(_sanitize_mark({
                "kind": "timeline",
                "part": _next_part(),
                "title": f"{topic.title()[:28]} · Timeline",
                "events": events,
            }))

    # Interactive Quiz (`drawQuiz`)
    if vtype == "quiz" and isinstance(parsed.get("questions"), list) and parsed["questions"]:
        qs = [
            {
                "text": str(q["text"])[:140],
                "options": [str(o)[:60] for o in q["options"][:4]],
                "right": int(q.get("right") or 0),
                "explain": str(q.get("explain") or "")[:140],
            }
            for q in parsed["questions"][:5]
            if isinstance(q, dict) and q.get("text") and isinstance(q.get("options"), list)
        ]
        if qs:
            primary_marks.append(_sanitize_mark({
                "kind": "quiz",
                "part": _next_part(),
                "title": f"{topic.title()[:28]} · Quiz",
                "questions": qs,
            }))

    # Table (`drawTable`)
    tbl_data = parsed.get("table") if isinstance(parsed.get("table"), dict) else None
    if tbl_data and isinstance(tbl_data.get("columns"), list) and isinstance(tbl_data.get("rows"), list) and tbl_data["rows"]:
        cols = [str(c)[:28] for c in tbl_data["columns"][:5]]
        rows = [[str(cell)[:70] for cell in r[:len(cols)]] for r in tbl_data["rows"][:8] if isinstance(r, list)]
        if cols and rows:
            primary_marks.append(_sanitize_mark({
                "kind": "table",
                "part": _next_part(),
                "title": str(tbl_data.get("title") or f"{topic.title()[:24]} · Reference")[:36],
                "columns": cols,
                "rows": rows,
            }))

    for mk in primary_marks[:3]:
        beats.append({
            "caption": summary if not beats else f"Breaking down {topic} step by step.",
            "marks": [mk],
        })

    highlights = [str(h)[:90] for h in (parsed.get("highlights") or [])[:4] if str(h).strip()]
    beats.append({
        "caption": summary,
        "marks": [_sanitize_mark({
            "kind": "card",
            "part": _next_part(),
            "title": topic.title()[:40],
            "heading": topic.title()[:32],
            "text": summary,
            "items": highlights,
            "tone": "teal",
        })],
    })

    return beats, summary


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
    diagram_actions = {"diagram", "flow", "graph", "bars", "table", "timeline", "math", "geometry", "molecule", "map", "quiz", "model"}
    screen_actions = {"circle", "arrow", "box", "underline", "clear", "point_out", "point", "highlight", "ring", "spotlight"}
    act = str(action or "").strip().lower()

    if act in {"point_out", "point"}:
        act = "ring"
    if act not in (diagram_actions | screen_actions):
        act = "diagram" if (topic or label) else "ring"

    if act == "clear":
        event: Dict[str, Any] = {"type": "ink_clear", "all": True}
        subscribers = push_overlay_event(event)
        return {"status": "success", "drawn": "clear", "engine": "roamies-ink-js", "subscribers": subscribers, "event": event}

    if act in diagram_actions:
        diag_topic = str(topic or label or "concept").strip()
        preferred = None if act == "diagram" else act
        if isinstance(beats, list) and beats:
            diag_beats = [
                {"caption": b.get("caption", ""), "marks": [_sanitize_mark(m) for m in (b.get("marks") or [])]}
                for b in beats if isinstance(b, dict)
            ]
            summary = f"Drew a visual diagram for {diag_topic}."
        else:
            diag_beats, summary = _generate_visual_diagram_beats(diag_topic, preferred_kind=preferred)

        event = {
            "type": "ink",
            "id": f"ink-{int(time.time() * 1000)}",
            "seg": 0,
            "last": True,
            "fade": max(22.0, float(duration)),
            "beats": diag_beats,
        }
        subscribers = push_overlay_event(event)
        play_overlay_sfx("ink")
        return {
            "status": "success",
            "drawn": act,
            "engine": "roamies-ink-js",
            "topic": diag_topic,
            "summary": summary,
            "subscribers": subscribers,
            "event": event,
        }

    resolved_rect = _resolve_target_rect(label) if (label and normalized and x == 500 and y == 500) else None
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
    elif act in {"highlight", "spotlight", "ring"}:
        rect = resolved_rect or [px - 50, py - 22, 100, 44]
        mark = {"kind": act, "at": {"rect": rect}, "tone": tone}
    else:
        rect = resolved_rect or [px - 45, py - 45, 90, 90]
        mark = {"kind": "circle", "at": {"rect": rect}, "tone": tone}

    if label:
        mark["label"] = str(label)[:40]
    mark["part"] = "p1"

    event = {
        "type": "ink",
        "id": f"ink-{int(time.time() * 1000)}",
        "seg": 0,
        "last": True,
        "fade": max(3.0, float(duration)),
        "beats": [{"marks": [mark]}],
    }
    subscribers = push_overlay_event(event)
    play_overlay_sfx("ink")
    return {
        "status": "success",
        "drawn": act,
        "engine": "roamies-ink-js",
        "coordinates": {"x": x, "y": y, "normalized": normalized},
        "resolved_rect": resolved_rect,
        "auto_fade_seconds": duration,
        "label": label,
        "subscribers": subscribers,
        "event": event,
    }


def screen_pen_guide(
    steps: list[dict[str, Any] | str],
    app: str | None = None,
    duration: float = 14.0,
) -> Dict[str, Any]:
    """Draw a multi-step Roamies visual walkthrough (`ring` + `caption`) over live UI targets."""
    if not isinstance(steps, list) or not steps:
        return {"status": "error", "code": "NO_STEPS", "error": "steps must be a non-empty list."}

    tones = ["teal", "amber", "sky", "coral"]
    beats: list[dict[str, Any]] = []
    resolved_count = 0

    for idx, raw_step in enumerate(steps[:8]):
        if isinstance(raw_step, str):
            target = raw_step.strip()
            caption = f"Step {idx + 1}: {target}"
        elif isinstance(raw_step, dict):
            target = str(raw_step.get("target") or raw_step.get("label") or "").strip()
            caption = str(raw_step.get("caption") or f"Step {idx + 1}: {target}").strip()
        else:
            continue

        rect = _resolve_target_rect(target, app=app) if target else None
        tone = tones[idx % len(tones)]
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
                "title": f"Step {idx + 1}",
                "text": caption[:120],
                "tone": tone,
            })
        beats.append({"marks": marks, "caption": caption[:140]})

    if not beats:
        return {"status": "error", "code": "NO_VALID_STEPS", "error": "No valid guide steps."}

    event = {
        "type": "ink",
        "id": f"guide-{int(time.time() * 1000)}",
        "seg": 0,
        "last": True,
        "fade": max(4.0, float(duration)),
        "beats": beats,
    }
    subscribers = push_overlay_event(event)
    play_overlay_sfx("ink")
    return {
        "status": "success",
        "drawn": "guide",
        "engine": "roamies-ink-js",
        "step_count": len(beats),
        "resolved_targets": resolved_count,
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
            topic=payload.get("topic"),
            beats=payload.get("beats"),
        )
