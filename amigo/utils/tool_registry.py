"""
Tool Registry & Action Dispatcher for Amigo Voice Assistant.
- Schema-driven single-step and multi-step LLM intent routing (zero hardcoded phrase/keyword rules).
- Static system prompt prefix for KV-cache reuse in llama.cpp.
- Pydantic v2 validation, inter-step output chaining ($prev / {{prev}}), and confirmation resumption.
- Roamies floating desktop overlay UI emitters.
"""

from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor
import datetime
import difflib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import amigo.core.rag_engine as rag_engine
import amigo.services.os_automation as os_automation
from amigo.core.reminder_timer import (
    get_active_data,
    handle_cancel_reminder,
    handle_list_reminders,
    handle_set_reminder,
    handle_set_timer,
    handle_snooze_reminder,
    parse_relative_seconds,
)
from amigo.services.app_opener import (
    execute_file_action,
    find_files,
    is_known_app,
    is_known_folder,
    open_folder,
    open_windows_app,
)
from amigo.services.content_generator import (
    generate_content as _tool_generate_content,
    insert_content as _tool_insert_content,
    regenerate_content as _tool_regenerate_content,
    revert_content as _tool_revert_content,
    type_text as _tool_type_text,
)
from amigo.services.weather import get_weather_data, weather_command
from amigo.services.web_search import (
    clean_search_query,
    format_for_llm,
    format_quote,
    get_stock_quote,
    resolve_youtube_video,
    search_web,
    searchGoogle,
)
from amigo.utils.calculate import Calc
from amigo.utils.network_utils import is_internet_connected
from amigo.utils.settings_resolver import open_setting

logger = logging.getLogger("amigo.tool_registry")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (ValueError, TypeError):
        return default


# Runtime-tunable settings (env-configurable, no scattered magic numbers)
_PENDING_TTL_SECONDS = _env_float("AMIGO_PENDING_CONFIRM_TTL", 30.0)
_WEATHER_TTL_SECONDS = _env_float("AMIGO_WEATHER_CACHE_TTL", 300.0)
_AUDIO_CACHE_TTL_SECONDS = _env_float("AMIGO_AUDIO_CACHE_TTL", 1.5)
_MEDIA_CACHE_TTL_SECONDS = _env_float("AMIGO_MEDIA_CACHE_TTL", 15.0)
_ROUTE_MAX_TOKENS = _env_int("AMIGO_ROUTE_MAX_TOKENS", 420)
_REPAIR_MAX_TOKENS = _env_int("AMIGO_REPAIR_MAX_TOKENS", 300)
_ANSWER_MAX_TOKENS = _env_int("AMIGO_ANSWER_MAX_TOKENS", 650)
_VISUAL_SUMMARY_MAX_TOKENS = _env_int("AMIGO_VISUAL_SUMMARY_MAX_TOKENS", 140)
_POWER_GRACE_SECONDS = _env_int("AMIGO_POWER_GRACE_SECONDS", 30)
_FILE_RESULT_LIMIT = _env_int("AMIGO_FILE_RESULT_LIMIT", 5)
_GROUNDING_SIMILARITY_THRESHOLD = _env_float("AMIGO_GROUNDING_SIMILARITY", 0.78)
_RAG_CONTEXT_SCORE_THRESHOLD = _env_float("AMIGO_RAG_SCORE_THRESHOLD", 0.35)
_MEMORY_SCORE_THRESHOLD = _env_float("AMIGO_MEMORY_SCORE_THRESHOLD", 0.40)


# ── Pydantic v2 Routing Models ─────────────────────────────────

class ToolCallModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    tool: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    speak: str = ""


class ActionPlanModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    actions: list[ToolCallModel] = Field(default_factory=list)


# ── Safe Lazy Forwarders (Prevents Circular Imports with llm_agent) ──────────

def get_ai_response(*args, **kwargs):
    from amigo.core.llm_agent import get_ai_response as _impl
    return _impl(*args, **kwargs)


def query_local_llm(*args, **kwargs):
    from amigo.core.llm_agent import query_local_llm as _impl
    return _impl(*args, **kwargs)


def sanitize_for_tts(*args, **kwargs):
    from amigo.core.llm_agent import sanitize_for_tts as _impl
    return _impl(*args, **kwargs)


def set_last_screen_text(*args, **kwargs):
    from amigo.core.llm_agent import set_last_screen_text as _impl
    return _impl(*args, **kwargs)


def build_turn_runtime_context(*args, **kwargs):
    from amigo.core.llm_agent import build_turn_runtime_context as _impl
    return _impl(*args, **kwargs)


def build_unified_history_messages(*args, **kwargs):
    from amigo.core.llm_agent import build_unified_history_messages as _impl
    return _impl(*args, **kwargs)


def update_active_state(slot: str, data: Any) -> None:
    if slot == "found_files":
        if isinstance(data, list):
            data = data[:_FILE_RESULT_LIMIT]
        elif isinstance(data, dict) and isinstance(data.get("items"), list):
            data["items"] = data["items"][:_FILE_RESULT_LIMIT]
    rag_engine.update_active_state(slot, data)


def get_active_state(clean_expired: bool = False) -> dict:
    return rag_engine.get_active_state(clean_expired=clean_expired) or {}


def _as_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()


# ── Pre-compiled Structural Patterns & Synchronized State ──────

_RE_URLS = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]*[^\s<>"{}|\\^`\[\].,;:!?]')
_RE_MEDIA_CLEAN_TITLE = re.compile(r"^(?:play|playing)\s*:\s*", re.IGNORECASE)
_RE_PREV_PLACEHOLDER = re.compile(r"(?:\$prev|\{\{\s*prev\s*\}\})", re.IGNORECASE)

_state_lock = threading.RLock()
_media_update_cb = None
_ui_broadcast_cb = None
_call_ctx = threading.local()
_PENDING_CONFIRMATION: dict[str, Any] | None = None
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="amigo-io")


def set_media_update_callback(cb):
    global _media_update_cb
    with _state_lock:
        _media_update_cb = cb


def _notify_media_update(payload: dict) -> None:
    with _state_lock:
        cb = _media_update_cb
    if cb:
        try:
            cb(payload)
        except Exception as exc:
            logger.debug("[Media Callback] Error invoking media update callback: %s", exc)


def set_ui_broadcast_callback(cb):
    global _ui_broadcast_cb
    with _state_lock:
        _ui_broadcast_cb = cb


def _broadcast_ui(event: str, payload: dict) -> bool:
    with _state_lock:
        cb = _ui_broadcast_cb
    if not cb:
        return False
    try:
        cb(event, payload)
        return True
    except Exception as exc:
        logger.debug("[UI Broadcast] Error broadcasting '%s': %s", event, exc)
        return False


def has_urls(text: str) -> bool:
    return bool(_RE_URLS.findall(text or ""))


def extract_and_open_urls(text: str) -> bool:
    urls = _RE_URLS.findall(text or "")
    if not urls:
        return False
    opened_any = False
    for url in urls:
        try:
            if webbrowser.open(url) is not False:
                opened_any = True
        except Exception as exc:
            logger.debug("[URL Opener] Failed opening %s: %s", url, exc)
    return opened_any


def _get_conv_history(explicit_history: list | None = None) -> list[dict]:
    if explicit_history is not None:
        return explicit_history
    call = getattr(_call_ctx, "value", None) or {}
    if call.get("conversation_history") is not None:
        return call["conversation_history"]
    return rag_engine.get_recent_conversations(count=5)


def _offline_reply(query: str, what: str) -> str:
    return f"You're offline right now, so {what}."


# ═══════════════════════════════════════════════════════════════
#  Roamies Floating Canvas UI Emitters
# ═══════════════════════════════════════════════════════════════

def _clean_markdown_markers(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\*\*|__|`|#+\s*", "", str(text)).strip()


def _format_roamies_card_content(raw_text: str) -> tuple[str, list[str]]:
    if not raw_text:
        return "", []
    clean = _clean_markdown_markers(raw_text)
    lines = [line.strip().lstrip("-*•0123456789. ") for line in clean.split("\n") if line.strip()]
    if len(lines) > 1:
        lead = _clean_markdown_markers(lines[0])
        items = [_clean_markdown_markers(it) for it in lines[1:9] if _clean_markdown_markers(it)]
        return lead, items
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    if len(sentences) > 1:
        lead = _clean_markdown_markers(sentences[0])
        items = [_clean_markdown_markers(it) for it in sentences[1:9] if _clean_markdown_markers(it)]
        return lead, items
    return clean, []


_format_roamies_sections = _format_roamies_card_content


def _push_roamies_ink(
    event_id: str,
    beats: list[dict[str, Any]],
    duration: float = 16.0,
    clear_previous: bool = False,
    stop_previous: bool = False,
) -> None:
    from amigo.services.screen_pen import push_overlay_event

    if clear_previous:
        clear_payload: dict[str, Any] = {"type": "ink_clear", "all": True}
        if stop_previous:
            clear_payload["stop"] = True
        push_overlay_event(clear_payload)

    push_overlay_event({
        "type": "ink",
        "id": event_id,
        "seg": 0,
        "last": True,
        "fade": float(duration or 16.0),
        "beats": beats,
    })


def emit_roamies_card(
    title: str,
    heading: str | None = None,
    text: str | None = None,
    items: list[str] | None = None,
    tone: str | None = None,
    duration: float = 16.0,
    sources: list[dict[str, str]] | None = None,
    pinned: bool = False,
    part: str | None = None,
    clear_previous: bool = False,
) -> None:
    try:
        from amigo.services.screen_pen import _VALID_TONES

        card_tone = tone if (tone and tone in _VALID_TONES) else "teal"
        card_text = _as_str(text)
        card_items = list(items or [])
        if card_text and not card_items:
            card_text, card_items = _format_roamies_card_content(card_text)

        clean_title = _clean_markdown_markers(_as_str(title, "Amigo"))[:40]
        clean_heading = _clean_markdown_markers(_as_str(heading or title))[:40]
        clean_lead = _clean_markdown_markers(card_text)
        clean_items = [_clean_markdown_markers(str(it)) for it in card_items if _as_str(it)][:8]

        card_part = _as_str(part) or f"card_{int(time.time() * 1000)}"
        mark: dict[str, Any] = {
            "kind": "card",
            "part": card_part,
            "title": clean_title,
            "heading": clean_heading,
            "text": clean_lead,
            "items": clean_items,
            "tone": card_tone,
            "pinned": bool(pinned),
        }
        if sources:
            mark["sources"] = sources

        _push_roamies_ink(
            event_id=card_part,
            duration=duration,
            clear_previous=clear_previous,
            beats=[{
                "voiced": True,
                "caption": clean_heading or clean_title,
                "marks": [mark, {"kind": "sfx", "name": "whoosh"}],
            }],
        )
    except Exception as exc:
        logger.debug("[Roamies Card] Could not push overlay event: %s", exc)


def emit_interactive_stock(quote: Any, duration: float = 22.0) -> None:
    try:
        from amigo.services.screen_pen import emit_avatar_act

        sym = getattr(quote, "symbol", "") or "STOCK"
        name = getattr(quote, "name", "") or sym
        price = float(getattr(quote, "price", 0.0) or 0.0)
        curr = getattr(quote, "currency", "$") or "$"
        change = float(getattr(quote, "change", 0.0) or 0.0)
        change_pct = float(getattr(quote, "change_pct", 0.0) or 0.0)
        day_low = float(getattr(quote, "day_low", price) or price)
        day_high = float(getattr(quote, "day_high", price) or price)
        prev_close = float(getattr(quote, "previous_close", price) or price)
        stock_tone = "lime" if change >= 0 else "coral"
        price_str = f"{price:,.2f}"
        pct_sign = "+" if change >= 0 else ""
        pct_str = f" ({pct_sign}{change_pct:.2f}%)"

        stock_items = [f"Change: {pct_sign}{change:,.2f}{pct_str}"]
        if day_low and day_high:
            stock_items.append(f"Day Range: {day_low:,.2f} - {day_high:,.2f}")
        vol = getattr(quote, "volume", None)
        if vol:
            stock_items.append(f"Volume: {int(vol):,}")

        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-stock-card",
            "title": f"{name} ({sym})"[:40],
            "heading": f"{curr} {price_str}{pct_str}"[:36],
            "text": f"Real-time market quote on {getattr(quote, 'exchange', 'Market') or 'Market'}.",
            "items": stock_items,
            "tone": stock_tone,
        }

        history = getattr(quote, "history", None)
        if isinstance(history, list) and len(history) >= 2:
            raw_pts = [float(p) for p in history if p is not None]
            step = max(1, len(raw_pts) // 30)
            sampled = raw_pts[::step]
            if sampled and sampled[-1] != raw_pts[-1]:
                sampled.append(raw_pts[-1])
            pts = [[float(i), round(p, 2)] for i, p in enumerate(sampled)]
            min_p, max_p = min(sampled), max(sampled)
        else:
            pts = [[0.0, prev_close], [1.0, day_low], [2.0, day_high], [3.0, price]]
            min_p = min(prev_close, day_low, day_high, price)
            max_p = max(prev_close, day_low, day_high, price)

        margin = max((max_p - min_p) * 0.08, 0.5)
        graph_mark: dict[str, Any] = {
            "kind": "graph",
            "part": "amigo-stock-graph",
            "title": f"{sym} · Trend",
            "x_range": [0.0, float(len(pts) - 1)],
            "y_range": [round(min_p - margin, 2), round(max_p + margin, 2)],
            "plots": [{"points": pts, "tone": stock_tone, "label": f"{sym} Trend"}],
            "dots": [{"x": float(len(pts) - 1), "y": round(price, 2), "label": f"{curr}{price_str}"}],
        }

        beats: list[dict[str, Any]] = [
            {
                "voiced": True,
                "caption": f"{sym} is trading at {curr} {price_str}",
                "marks": [card_mark, {"kind": "sfx", "name": "whoosh"}],
            },
            {
                "voiced": False,
                "caption": f"{sym} intraday price trend",
                "marks": [graph_mark, {"kind": "sfx", "name": "scan"}],
            },
        ]
        if change >= 0:
            beats.append({
                "voiced": False,
                "caption": f"{sym} positive surge",
                "marks": [{"kind": "emote", "name": "hype"}],
            })

        _push_roamies_ink(event_id="amigo-stock", duration=duration, clear_previous=True, beats=beats)
        emit_avatar_act("hype" if change >= 0 else "motivate")
    except Exception as exc:
        logger.debug("[Interactive Stock] Error emitting: %s", exc)


def emit_interactive_setting(setting_name: str, status_desc: str, is_on: bool, duration: float = 18.0) -> None:
    try:
        from amigo.services.screen_pen import emit_avatar_act

        s_title = setting_name.title()
        clean_key = re.sub(r"[^a-z0-9]", "", setting_name.lower())
        tone = "lime" if is_on else "coral"
        action_verb = "Turn Off" if is_on else "Turn On"

        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-setting-card",
            "title": f"Setting · {s_title}"[:40],
            "heading": f"{s_title}: {'Enabled' if is_on else 'Disabled'}"[:36],
            "text": _as_str(status_desc)[:300],
            "items": [
                f"Current Status: {'Active / On' if is_on else 'Disabled / Off'}",
                "Click button below to toggle directly on screen.",
            ],
            "tone": tone,
        }
        btn_mark: dict[str, Any] = {
            "kind": "button",
            "part": f"act-toggle-{clean_key}",
            "text": f"{action_verb} {s_title}",
            "tone": "accent" if is_on else "lime",
        }

        _push_roamies_ink(
            event_id="amigo-setting",
            duration=duration,
            clear_previous=True,
            stop_previous=True,
            beats=[
                {
                    "voiced": True,
                    "caption": f"{s_title} is {'on' if is_on else 'off'}",
                    "marks": [card_mark, {"kind": "sfx", "name": "whoosh"}],
                },
                {
                    "voiced": False,
                    "caption": f"Toggle {s_title}",
                    "marks": [btn_mark, {"kind": "sfx", "name": "pop"}],
                },
            ],
        )
        emit_avatar_act("hype" if is_on else "wave")
    except Exception as exc:
        logger.debug("[Interactive Setting] Error emitting: %s", exc)


def _emit_interactive_slider(
    prefix: str,
    title: str,
    label: str,
    level: int | float,
    bar_tone: str,
    down_part: str,
    down_text: str,
    down_tone: str,
    up_part: str,
    up_text: str,
    up_tone: str,
    caption_prefix: str,
    adjust_caption: str,
    duration: float = 16.0,
) -> None:
    try:
        clamped = max(0, min(100, int(level)))
        bars_mark: dict[str, Any] = {
            "kind": "bars",
            "part": f"amigo-{prefix}-bars",
            "title": title,
            "bars": [{"label": label, "value": clamped, "tone": bar_tone}],
            "unit": "%",
        }
        btn_down: dict[str, Any] = {"kind": "button", "part": down_part, "text": down_text, "tone": down_tone}
        btn_up: dict[str, Any] = {"kind": "button", "part": up_part, "text": up_text, "tone": up_tone}
        _push_roamies_ink(
            event_id=f"{prefix}-{int(time.time() * 1000)}",
            duration=duration,
            clear_previous=True,
            beats=[
                {
                    "voiced": True,
                    "caption": f"{caption_prefix} at {clamped}%",
                    "marks": [bars_mark, {"kind": "sfx", "name": "scan"}],
                },
                {
                    "voiced": False,
                    "caption": adjust_caption,
                    "marks": [btn_down, btn_up, {"kind": "sfx", "name": "pip"}],
                },
            ],
        )
    except Exception as exc:
        logger.debug("[Interactive %s] Error emitting: %s", title, exc)


def emit_interactive_volume(level: int | float, duration: float = 16.0) -> None:
    _emit_interactive_slider(
        prefix="vol", title="Audio Volume", label="Volume Level", level=level,
        bar_tone="sky", down_part="act-vol-down", down_text="[-] Down 10%", down_tone="sky",
        up_part="act-vol-up", up_text="[+] Up 10%", up_tone="teal",
        caption_prefix="Volume", adjust_caption="Adjust volume", duration=duration,
    )


def emit_interactive_brightness(level: int | float, duration: float = 16.0) -> None:
    _emit_interactive_slider(
        prefix="bright", title="Display Brightness", label="Brightness", level=level,
        bar_tone="amber", down_part="act-bright-down", down_text="[-] Dimmer", down_tone="amber",
        up_part="act-bright-up", up_text="[+] Brighter", up_tone="lime",
        caption_prefix="Brightness", adjust_caption="Adjust brightness", duration=duration,
    )


def emit_interactive_weather(city_name: str, temp: Any, condition: str, humidity: Any, wind: Any, duration: float = 20.0) -> None:
    try:
        def _to_int(val: Any, fallback: int) -> int:
            if val is None or str(val) in ("0", "--", "unknown", ""):
                return fallback
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return fallback

        t_val = _to_int(temp, 20)
        h_val = _to_int(humidity, 50)
        w_val = _to_int(wind, 10)

        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-weather-card",
            "title": f"Weather · {city_name}"[:40],
            "heading": f"{temp}°C · {condition}"[:36],
            "text": f"Live meteorological report for {city_name}.",
            "items": [
                f"Temperature: {temp}°C",
                f"Sky Condition: {condition}",
                f"Relative Humidity: {h_val}%",
                f"Wind Velocity: {w_val} km/h",
            ],
            "tone": "sky",
        }
        bars_mark: dict[str, Any] = {
            "kind": "bars",
            "part": "amigo-weather-bars",
            "title": f"Atmosphere · {city_name}",
            "bars": [
                {"label": "Temp (°C)", "value": t_val, "tone": "sky"},
                {"label": "Humidity (%)", "value": h_val, "tone": "teal"},
                {"label": "Wind (km/h)", "value": w_val, "tone": "coral"},
            ],
        }

        _push_roamies_ink(
            event_id="amigo-weather",
            duration=duration,
            clear_previous=True,
            beats=[
                {
                    "voiced": True,
                    "caption": f"{temp}°C and {condition} in {city_name}",
                    "marks": [card_mark, {"kind": "sfx", "name": "whoosh"}],
                },
                {
                    "voiced": False,
                    "caption": f"Conditions in {city_name}",
                    "marks": [bars_mark, {"kind": "sfx", "name": "scan"}],
                },
            ],
        )
    except Exception as exc:
        logger.debug("[Interactive Weather] Error emitting: %s", exc)


def emit_interactive_reminders(reminders: list[dict], duration: float = 25.0) -> None:
    try:
        from amigo.services.screen_pen import emit_avatar_act, push_overlay_event

        items = [f"{r.get('message', 'Reminder')} ({r.get('target_time') or r.get('time', '')})" for r in (reminders or [])[:6]]
        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-rem-card",
            "title": "My Active Reminders",
            "heading": f"{len(reminders)} Reminder{'s' if len(reminders) != 1 else ''}" if reminders else "No Active Reminders",
            "text": f"You have {len(reminders)} scheduled task(s)." if reminders else "Your reminder list is clear.",
            "items": items if items else ["All caught up!"],
            "tone": "amber",
        }

        _push_roamies_ink(
            event_id="amigo-rem-summary",
            duration=duration,
            clear_previous=True,
            beats=[{
                "voiced": True,
                "caption": f"You have {len(reminders)} reminders scheduled",
                "marks": [card_mark, {"kind": "sfx", "name": "pop"}, {"kind": "emote", "name": "remind"}],
            }],
        )
        emit_avatar_act("remind")

        if reminders:
            today_items = [
                {
                    "id": str(r.get("id") or idx),
                    "text": str(r.get("message") or "Reminder"),
                    "when": str(r.get("target_time") or ""),
                    "done": bool(r.get("done", False)),
                }
                for idx, r in enumerate(reminders[:8])
            ]
            push_overlay_event({"type": "reminders", "today": today_items, "later": [], "seconds": int(duration)})
    except Exception as exc:
        logger.debug("[Interactive Reminders] Error emitting: %s", exc)


# ═══════════════════════════════════════════════════════════════
#  Document Path Resolution
# ═══════════════════════════════════════════════════════════════

def resolve_document_path(query_or_target: str, target_index: int | None = None) -> str | None:
    state = get_active_state(clean_expired=False)
    active_f = state.get("active_file")
    active_path = active_f.get("path") if isinstance(active_f, dict) else None
    active_ok = bool(active_path and os.path.exists(active_path))

    found_list = state.get("found_files", [])
    if isinstance(found_list, dict):
        found_list = found_list.get("items", [])

    if target_index is not None:
        if isinstance(found_list, list) and found_list:
            try:
                t_idx = int(target_index)
                if t_idx == -1:
                    t_idx = len(found_list) - 1
                if 0 <= t_idx < len(found_list) and os.path.exists(found_list[t_idx]):
                    return found_list[t_idx]
            except (ValueError, TypeError):
                pass
        return None

    raw = _as_str(query_or_target)
    if not raw:
        return active_path if active_ok else None
    if os.path.isfile(raw):
        return raw
    if len(raw) < 2:
        return None

    matches, _ = find_files(raw)
    if matches:
        update_active_state("active_file", {"path": matches[0], "name": os.path.basename(matches[0])})
        return matches[0]

    if len(raw) >= 3:
        results = rag_engine.search_files_by_context(raw, top_k=1)
        if results and results[0].get("score", 0) > _RAG_CONTEXT_SCORE_THRESHOLD:
            fpath = results[0].get("filepath")
            if fpath and os.path.exists(fpath):
                update_active_state("active_file", {"path": fpath, "name": os.path.basename(fpath)})
                return fpath

    return None


# ═══════════════════════════════════════════════════════════════
#  Tool Handlers (Context & Multi-Step Aware)
# ═══════════════════════════════════════════════════════════════

def _tool_web_search(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()

    raw_q = _as_str(params.get("query") or query) or query
    resolved_q = rag_engine.resolve_contextual_query(raw_q, conv_history)
    target = clean_search_query(resolved_q.replace("(", "").replace(")", "")) or raw_q
    show_in_browser = bool(params.get("show_in_browser"))

    update_active_state("last_search", {"query": target})
    update_active_state("active_subject", {"name": target, "category": "web_search"})

    tool_id = None
    try:
        from amigo.services.screen_pen import emit_tool_anim, end_tool_anim
        tool_id = emit_tool_anim("web", label=f"Searching: {target[:24]}", variant="search")
    except Exception as exc:
        logger.debug("[Web Search] Tool anim start skipped: %s", exc)

    try:
        search_response = search_web(target, max_results=6, fetch_content=False, fast_mode=True)
    except Exception as exc:
        logger.debug("[Web Search] search_web failed: %s", exc)
        if tool_id:
            try:
                from amigo.services.screen_pen import end_tool_anim
                end_tool_anim(tool_id, is_error=True)
            except Exception:
                pass
        return _offline_reply(query, "web search is unavailable"), None, {"status": "offline"}

    search_url = f"https://www.google.com/search?q={urllib.parse.quote(target)}"
    if show_in_browser:
        searchGoogle(target)

    if tool_id:
        try:
            from amigo.services.screen_pen import end_tool_anim
            end_tool_anim(tool_id, is_error=not bool(search_response.results))
        except Exception as exc:
            logger.debug("[Web Search] Tool anim end skipped: %s", exc)

    if search_response.results:
        web_context = format_for_llm(search_response, max_chars=3500)
        prompt_q = resolved_q if resolved_q and resolved_q != raw_q else query
        response = get_ai_response(
            prompt_q,
            web_context=web_context,
            conversation_history=conv_history,
            use_memory=True,
            max_tokens=_ANSWER_MAX_TOKENS,
        )
        top_sources = [
            {"title": r.title[:60], "url": r.url}
            for r in search_response.results[:4]
            if getattr(r, "url", None)
        ]
        source_items = [f"{r.title[:45]} ({r.domain})" for r in search_response.results[:4]]
        top_images = (search_response.structured_data or {}).get("images") or []
        if top_images:
            try:
                from amigo.services.screen_pen import emit_roamies_images
                emit_roamies_images(
                    title=f"Web · {target[:24]}",
                    images=top_images,
                    summary=response,
                    bullets=source_items,
                    duration=24.0,
                )
            except Exception as exc:
                logger.debug("[Web Search] emit_roamies_images error: %s", exc)
                emit_roamies_card(
                    title=f"Web · {target[:24]}", heading=target[:36], text=response,
                    items=source_items, tone="teal", duration=22.0, sources=top_sources,
                )
        else:
            emit_roamies_card(
                title=f"Web · {target[:24]}", heading=target[:36], text=response,
                items=source_items, tone="teal", duration=22.0, sources=top_sources,
            )
        meta = {
            "status": "success",
            "query_type": search_response.query_type,
            "results_count": len(search_response.results),
            "engines_used": search_response.engines_used,
            "search_time_ms": search_response.search_time_ms,
            "structured_data": search_response.structured_data,
            "cache_hit": search_response.cache_hit,
        }
        top_result = search_response.results[0]
        verified_url = top_result.url or search_url
        meta["top_url"] = verified_url
        meta["title"] = top_result.title
        update_active_state("last_link", {"url": verified_url, "title": top_result.title, "query": target})
        update_active_state("last_search", {"query": target, "url": verified_url, "title": top_result.title})
        return response, verified_url, meta

    if not getattr(search_response, "engines_used", None) and not is_internet_connected():
        return _offline_reply(query, "web search is unavailable"), None, {"status": "offline"}

    return f"I couldn't find any web results for '{target}'.", search_url, {"status": "no_results"}


def _tool_stock_quote(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    target = _as_str(params.get("symbol") or params.get("query") or query)

    try:
        quote = get_stock_quote(target)
    except Exception as exc:
        logger.debug("[Stock Quote] Lookup failed for '%s': %s", target, exc)
        quote = None

    if quote:
        update_active_state("active_subject", {"name": f"{quote.name} ({quote.symbol})", "category": "stock"})
        meta = {
            "status": "success",
            "symbol": quote.symbol,
            "name": quote.name,
            "price": quote.price,
            "currency": quote.currency,
            "change": quote.change,
            "change_pct": quote.change_pct,
            "previous_close": quote.previous_close,
            "exchange": quote.exchange,
            "url": quote.url,
            "day_high": quote.day_high,
            "day_low": quote.day_low,
            "volume": quote.volume,
            "fifty_two_week_high": quote.fifty_two_week_high,
            "fifty_two_week_low": quote.fifty_two_week_low,
            "history": quote.history,
            "timestamps": quote.timestamps,
            "as_of": quote.as_of.strftime("%b %d, %I:%M %p") if quote.as_of else "",
        }
        emit_interactive_stock(quote)
        return format_quote(quote), quote.url, meta

    if not is_internet_connected():
        return _offline_reply(query, "you cannot check market quotes offline"), None, {"status": "offline"}
    return f"I couldn't find a live stock quote for '{target}'.", None, {"status": "not_found"}


def _tool_open_website(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    raw_url = _as_str(params.get("url"))
    title = _as_str(params.get("title"))

    state = get_active_state(clean_expired=True)
    last_link_data = state.get("last_link") or state.get("last_search") or {}
    prior_url = _as_str(last_link_data.get("url"))

    if not raw_url and prior_url:
        raw_url = prior_url
        if not title and last_link_data.get("title"):
            title = _as_str(last_link_data["title"])

    if not (raw_url and ("." in raw_url or "://" in raw_url)):
        return "Which website would you like me to open?", None, {"status": "missing_url"}

    if re.match(r"^https?://", raw_url, re.I):
        url = raw_url
    elif "://" in raw_url or raw_url.lower().startswith(("javascript:", "file:", "data:")):
        return "I can only open regular web addresses.", None, {"status": "invalid_scheme"}
    else:
        url = f"https://{raw_url}"

    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        logger.debug("[Open Website] webbrowser.open failed for '%s': %s", url, exc)
        opened = False
    if opened is False:
        if not is_internet_connected():
            return _offline_reply(query, "websites cannot be opened"), None, {"status": "offline"}
        return f"I couldn't open {url} in your browser.", None, {"status": "error"}

    label = title or url
    update_active_state("active_subject", {"name": label, "category": "website", "url": url})
    update_active_state("last_link", {"name": label, "url": url, "title": label})
    return f"Opening {label}.", url, {"status": "success", "url": url, "title": label}


def _tool_play_youtube(params, query, spoken):
    q = _as_str((params or {}).get("query") or query) or query
    try:
        media_info = resolve_youtube_video(q)
    except Exception as exc:
        logger.debug("[YouTube] resolve_youtube_video failed for '%s': %s", q, exc)
        media_info = {}
    clean_q = media_info.get("query") or q

    if not media_info.get("url"):
        if not is_internet_connected():
            return _offline_reply(query, "you cannot stream YouTube videos offline"), None, {"status": "offline"}
        return f"I couldn't find a YouTube video for '{clean_q}'.", None, {"status": "not_found"}

    media_payload = {
        "title": media_info.get("title", clean_q.title()),
        "artist": media_info.get("channel", "YouTube"),
        "video_id": media_info.get("video_id", ""),
        "url": media_info.get("url", ""),
        "status": "playing",
    }
    _notify_media_update(media_payload)

    update_active_state("current_media", {
        "title": media_payload["title"],
        "artist": media_payload["artist"],
        "platform": "YouTube",
        "query": clean_q,
        "url": media_payload["url"],
        "status": "playing",
        "stale": False,
    })
    webbrowser.open(media_info["url"])

    title = media_info.get("title")
    channel = media_info.get("channel")
    by_ch = f" by {channel}" if channel and channel not in ("YouTube", "") else ""
    if media_info.get("is_live"):
        return f"Playing live stream: '{title}'{by_ch} on YouTube.", media_info["url"], media_payload
    if media_info.get("not_live_fallback"):
        return f"{clean_q} is not currently live. Playing their latest stream: '{title}' on YouTube.", media_info["url"], media_payload
    if title and title.strip().lower() != clean_q.strip().lower():
        return f"Playing '{title}'{by_ch} on YouTube.", media_info["url"], media_payload
    return spoken or f"Playing {clean_q} on YouTube.", media_info["url"], media_payload


def _tool_get_current_media(params, query, spoken):
    live_media = _get_live_media_title_from_windows(wait_if_cold=True)
    is_playing = _is_system_audio_playing()

    if live_media:
        raw_title, raw_artist, status = live_media
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", raw_title).strip() if raw_title else ""
        by_artist = f" by {raw_artist}" if raw_artist and raw_artist.lower() not in clean_title.lower() else ""
        update_active_state("current_media", {"title": clean_title, "artist": raw_artist, "status": status.lower()})
        if status.lower() == "paused":
            return f"'{clean_title}'{by_artist} is paused.", None, {"status": "paused", "title": clean_title}
        return f"Currently playing '{clean_title}'{by_artist}.", None, {"status": "playing", "title": clean_title}

    try:
        sys_media = os_automation.get_system_media_info()
        if sys_media and sys_media.get("title"):
            clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", sys_media["title"]).strip()
            raw_artist = sys_media.get("artist", "")
            by_artist = f" by {raw_artist}" if raw_artist and raw_artist.lower() not in clean_title.lower() else ""
            status = sys_media.get("status", "playing")
            update_active_state("current_media", {"title": clean_title, "artist": raw_artist, "status": status.lower()})
            if status.lower() == "paused":
                return f"'{clean_title}'{by_artist} is paused.", None, {"status": "paused", "title": clean_title}
            return f"Currently playing '{clean_title}'{by_artist}.", None, {"status": "playing", "title": clean_title}
    except Exception as exc:
        logger.debug("[Current Media] get_system_media_info failed: %s", exc)

    last_played = get_last_played_song()
    if last_played and last_played.get("title"):
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", last_played["title"]).strip()
        raw_artist = last_played.get("artist", "")
        by_artist = f" by {raw_artist}" if raw_artist and raw_artist.lower() not in clean_title.lower() else ""
        if is_playing:
            return f"Currently playing '{clean_title}'{by_artist}.", last_played.get("url"), {"status": "playing", "title": clean_title}
        return f"'{clean_title}'{by_artist} was played recently.", last_played.get("url"), {"status": "recent", "title": clean_title}

    media_state = (get_active_state(clean_expired=True)).get("current_media") or {}
    title = media_state.get("title") or media_state.get("query") or ""
    if title:
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", title).strip()
        artist = media_state.get("artist") or ""
        by_artist = f" by {artist}" if artist and artist.lower() not in clean_title.lower() else ""
        if clean_title:
            return f"Currently playing '{clean_title}'{by_artist}.", media_state.get("url"), {"status": "playing", "title": clean_title}

    if is_playing:
        return "Audio is currently playing on your PC, but the track name could not be identified.", None, {"status": "unknown_audio"}
    return "No song or video is currently playing.", None, {"status": "idle"}


def _tool_get_time(params, query, spoken):
    tz_name = _as_str((params or {}).get("timezone")) if isinstance(params, dict) else ""
    now = datetime.datetime.now()
    where = ""
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo(tz_name))
            where = f" in {tz_name.split('/')[-1].replace('_', ' ')}"
        except Exception as exc:
            logger.debug("[Time Tool] Invalid timezone '%s': %s", tz_name, exc)
            return (
                f"I couldn't look up the time zone '{tz_name}', so here is your local time: {now.strftime('%I:%M %p').lstrip('0')}.",
                None,
                {"status": "timezone_fallback"},
            )
    return f"It is currently {now.strftime('%I:%M %p').lstrip('0')}{where}.", None, {"status": "success"}


def _tool_get_date(params, query, spoken):
    now = datetime.datetime.now()
    return f"Today is {now.strftime('%A, %B')} {now.day}, {now.year}.", None, {"status": "success"}


def _tool_time_date(params, query, spoken):
    action = _as_str((params or {}).get("action") or (params or {}).get("kind") or "both").lower()
    if action == "time":
        return _tool_get_time(params, query, spoken)
    if action == "date":
        return _tool_get_date(params, query, spoken)
    now = datetime.datetime.now()
    return f"It is {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B')} {now.day}, {now.year}.", None, {"status": "success"}


def _tool_get_datetime(params, query, spoken):
    kind = _as_str((params or {}).get("kind") or "time").lower()
    if kind == "date":
        return _tool_get_date(params, query, spoken)
    if kind == "both":
        return _tool_time_date(params, query, spoken)
    return _tool_get_time(params, query, spoken)


_WEATHER_CACHE: collections.OrderedDict[str, tuple[float, dict]] = collections.OrderedDict()
_WEATHER_CACHE_LOCK = threading.Lock()
_WEATHER_CACHE_MAX = 32


def _cached_weather_data(city: str) -> dict:
    key = _as_str(city).lower()
    now = time.time()
    with _WEATHER_CACHE_LOCK:
        hit = _WEATHER_CACHE.get(key)
        if hit and (now - hit[0]) < _WEATHER_TTL_SECONDS:
            _WEATHER_CACHE.move_to_end(key)
            return dict(hit[1])

    data = get_weather_data(city or "")
    if isinstance(data, dict) and data:
        with _WEATHER_CACHE_LOCK:
            expired = [k for k, v in _WEATHER_CACHE.items() if (now - v[0]) >= _WEATHER_TTL_SECONDS]
            for k in expired:
                _WEATHER_CACHE.pop(k, None)
            _WEATHER_CACHE[key] = (now, dict(data))
            while len(_WEATHER_CACHE) > _WEATHER_CACHE_MAX:
                _WEATHER_CACHE.popitem(last=False)
        return data
    return {}


def _tool_get_weather(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    city = _as_str(params.get("city"))
    if not city:
        subj = (get_active_state(clean_expired=True)).get("active_subject") or {}
        if subj.get("category") == "weather" and subj.get("name"):
            city = subj["name"]

    w_data = _cached_weather_data(city)
    temp = (w_data.get("temp_c") or w_data.get("temperature")) if isinstance(w_data, dict) else None
    if w_data and w_data.get("success", True) and temp and temp not in ("--", "unknown"):
        city_name = w_data.get("city", city or "your area")
        condition = w_data.get("condition", "Clear")
        humidity = w_data.get("humidity")
        if humidity and str(humidity) not in ("0", "--"):
            result = f"Weather in {city_name}: {temp}°C, {condition} with {humidity}% humidity."
        else:
            result = f"Weather in {city_name}: {temp}°C, {condition}."
        update_active_state("active_subject", {"name": city_name, "category": "weather"})
        wind = w_data.get("wind_kmh") or w_data.get("wind_speed")
        emit_interactive_weather(city_name, temp, condition, humidity, wind)
        return result, None, {"status": "success", "city": city_name}

    try:
        result = weather_command(city if city else query)
    except Exception as exc:
        logger.debug("[Weather Tool] weather_command failed: %s", exc)
        result = ""
    if result:
        emit_interactive_weather(city or "Local", "--", "Report", 50, 10)
        return result, None, {"status": "success", "city": city or "Local"}
    if not is_internet_connected():
        return _offline_reply(query, "live weather cannot be fetched"), None, {"status": "offline"}
    return f"I couldn't fetch weather information for {city or 'your area'}.", None, {"status": "not_found"}


def is_app_already_running(app_name: str) -> bool:
    target = _as_str(app_name).lower()
    if not target:
        return False
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower().replace(".exe", "")
            if target == name or bool(re.search(rf"\b{re.escape(target)}\b", name)):
                return True
    except Exception as exc:
        logger.debug("[Process Check] psutil process iteration failed: %s", exc)
    return False


def _tool_open_app(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    app_name = _as_str(
        params.get("name")
        or params.get("app")
        or params.get("app_name")
        or params.get("target")
        or params.get("query")
    )
    if not app_name:
        return "Which application would you like to open?", None, {"status": "missing_app"}

    if not is_known_app(app_name) and not is_app_already_running(app_name):
        return (
            f"I couldn't find an app called '{app_name.title()}' installed on your PC. Which application would you like to open?",
            None,
            {"status": "not_found", "query": app_name},
        )
    if open_windows_app(app_name):
        update_active_state("active_app", {"name": app_name})
        return f"Opened {app_name.title()}.", None, {"status": "completed", "app": app_name}

    if is_app_already_running(app_name):
        update_active_state("active_app", {"name": app_name})
        return f"{app_name.title()} is already open.", None, {"status": "completed", "app": app_name}

    return f"I couldn't find an app called '{app_name.title()}' installed on your PC.", None, {"status": "not_found", "query": app_name}


def _tool_open_folder(params, query, spoken):
    folder_name = _as_str((params or {}).get("name"))
    if not folder_name or not is_known_folder(folder_name):
        return "Which folder would you like to open?", None, {"status": "missing_folder"}
    ok, msg = open_folder(folder_name)
    return (msg or spoken), None, {"status": "success" if ok else "error"}


def _tool_find_file(params, query, spoken):
    q = _as_str((params or {}).get("query") or query)
    matches, summary = find_files(q)
    file_results = []
    seen_paths: set[str] = set()

    for path in matches:
        try:
            stat = os.stat(path)
            seen_paths.add(path)
            file_results.append({
                "path": path,
                "name": os.path.basename(path),
                "extension": os.path.splitext(path)[1].lower() or "file",
                "folder": os.path.dirname(path),
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except (OSError, TypeError):
            continue

    if len(file_results) < 3:
        for r in rag_engine.search_files_by_context(q, top_k=_FILE_RESULT_LIMIT):
            fp = r.get("filepath", "")
            if fp and fp not in seen_paths and os.path.exists(fp):
                try:
                    stat = os.stat(fp)
                    seen_paths.add(fp)
                    file_results.append({
                        "path": fp,
                        "name": r.get("filename", os.path.basename(fp)),
                        "extension": os.path.splitext(fp)[1].lower() or "file",
                        "folder": os.path.dirname(fp),
                        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "score": r.get("score", 0.0),
                    })
                except OSError:
                    continue

    if file_results:
        update_active_state("active_file", {"path": file_results[0]["path"], "name": file_results[0]["name"]})
        update_active_state("found_files", [f["path"] for f in file_results[:_FILE_RESULT_LIMIT]])
        file_items = [f"{f['name']} ({f.get('extension', 'file')})" for f in file_results[:6]]
        emit_roamies_card(
            title=f"Files · {len(file_results)} found",
            heading=f"Found {len(file_results)} File{'s' if len(file_results) != 1 else ''}",
            text=f"Matching search results for '{q}'.",
            items=file_items,
            tone="teal",
            duration=18.0,
        )
        lines = [f"I found {len(file_results)} matching file{'s' if len(file_results) != 1 else ''}:"]
        for i, f in enumerate(file_results[:_FILE_RESULT_LIMIT], 1):
            clean_t = os.path.splitext(f["name"])[0].replace("_", " ").replace("-", " ").title()
            lines.append(f"{i}. {clean_t} ({f['extension']})")
        return "\n".join(lines), None, {"status": "success", "file_results": file_results}

    return summary or f"No files found matching '{q}'.", None, {"status": "no_results", "file_results": []}


def _tool_take_screenshot(params, query, spoken):
    out_path = _as_str((params or {}).get("path")) or "amigo_screenshot.png"
    try:
        from amigo.services.screen_vision import capture_screen_image
        capture_screen_image(out_path)
        return "Screenshot saved.", None, {"status": "success", "path": out_path}
    except Exception as e:
        logger.debug("[Screenshot] Failed: %s", e)
        return f"Failed to take screenshot: {e}", None, {"status": "error", "error": str(e)}


def _tool_read_screen(params, query, spoken):
    q = params.get("question", query) if isinstance(params, dict) else query
    try:
        from amigo.services.screen_vision import inspect_screen
        res = inspect_screen(q)
        screen_text = res.get("screen_text", "")
        if screen_text:
            set_last_screen_text(screen_text)
        win_title = res.get("window_title", "Active Window")
        reply_snippet = res.get("reply", "I inspected your screen.")
        screen_items = [f"Window: {win_title}"]
        if screen_text:
            screen_items.append(f"Snippet: {screen_text[:120]}...")
        emit_roamies_card(
            title=f"Screen Vision · {win_title}",
            heading="Screen Inspected",
            text=reply_snippet[:240],
            items=screen_items,
            tone="lilac",
            duration=18.0,
        )
        return res.get("reply", "I inspected your screen."), None, {
            "status": "success",
            "window_title": win_title,
            "screenshot": res.get("screenshot_path", ""),
            "text_snippet": screen_text[:300],
            "screen_text": screen_text,
        }
    except Exception as exc:
        logger.debug("[Read Screen] Failed: %s", exc)
        return "Could not inspect the screen right now.", None, {"status": "error"}


def _tool_read_window(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    target = _as_str(params.get("target") or params.get("app") or "front")
    t = "front" if (not target or target.lower() in ("front", "current", "active")) else f"app:{target}"
    from amigo.services import hands
    if not hands.is_available():
        return "roamies-hands helper is not available.", None, {"ok": False}
    res = hands.ui(t)
    if not res.get("ok"):
        return f"Could not inspect window for '{target}': {res.get('error', 'unknown error')}", None, res
    win = res.get("window") or {}
    controls = res.get("controls") or []
    texts = res.get("texts") or []
    elsewhere = res.get("elsewhere") or []
    app_name = win.get("app") or target
    title = win.get("title") or ""
    lines = [f"{app_name}{f' — {title}' if title else ''}"]
    if controls:
        lines.append(f"Found {len(controls)} controls:")
        for c in controls[:30]:
            cid = c.get("id", "")
            role = c.get("role", "")
            lbl = c.get("label") or c.get("name") or ""
            val = c.get("value")
            off = " (offscreen)" if c.get("offscreen") else ""
            val_str = f' = "{val}"' if val else ""
            lines.append(f"  [{cid}] {role} \"{lbl}\"{val_str}{off}")
    if texts:
        lines.append("\nVisible text: " + " | ".join(str(t_item) for t_item in texts[:15]))
    if elsewhere:
        lines.append("\nElsewhere (scrollable): " + " | ".join(str(e_item) for e_item in elsewhere[:10]))
    return "\n".join(lines), None, res


def _tool_memory_recall(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = _as_str(params.get("action")).lower()
    conv_history = _get_conv_history()
    q = _as_str(params.get("query") or query)
    q_low = q.lower()

    fact = _as_str(params.get("fact"))
    if action == "store" or fact:
        if not fact:
            fact = q
        rag_engine.add_user_fact(fact, category="user_memory")
        return f"I've remembered that: {fact}.", None, {"status": "stored", "fact": fact}

    recalled_facts = []
    recalled = rag_engine.search(
        q,
        target_collections=[rag_engine.USER_FACTS, rag_engine.CONVERSATIONS],
        top_k=_FILE_RESULT_LIMIT,
        query_type="conversation_recall",
        conversation_history=conv_history,
    )
    for r in recalled:
        if r.get("score", 0) >= _MEMORY_SCORE_THRESHOLD and r.get("text"):
            recalled_facts.append(r["text"])

    prefs = rag_engine.load_profile().get("preferences", {})
    for k, v in prefs.items():
        if str(k).lower() in q_low or (_as_str(v) and str(v).lower() in q_low):
            recalled_facts.append(f"{k}: {v}")

    if recalled_facts:
        mem_context = "Remembered User Facts & Conversations:\n" + "\n".join(f"- {f}" for f in recalled_facts)
        return (
            get_ai_response(q, doc_context=mem_context, conversation_history=conv_history),
            None,
            {"status": "recalled", "recalled_facts": recalled_facts[:3]},
        )

    return "I don't have anything saved about that yet.", None, {"status": "not_found"}


def _tool_ask_document(params, query, spoken, conversation_history=None):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history(conversation_history)
    filepath = _as_str(params.get("filepath") or params.get("filename") or params.get("file"))
    t_idx = params.get("target_index")
    question = _as_str(params.get("question") or params.get("query") or query) or query

    target_path = None
    if t_idx is not None:
        target_path = resolve_document_path("", target_index=t_idx)
    elif filepath:
        target_path = resolve_document_path(filepath)
    else:
        resolved_q = rag_engine.resolve_contextual_query(question, conv_history)
        if resolved_q != question:
            act_f = get_active_state(clean_expired=False).get("active_file")
            if isinstance(act_f, dict) and act_f.get("path") and os.path.exists(act_f["path"]):
                target_path = act_f["path"]

    if filepath and not target_path:
        matched_files = rag_engine.search_files_by_context(filepath, top_k=1)
        if matched_files and matched_files[0].get("filepath"):
            target_path = matched_files[0]["filepath"]

    tool_id = None
    try:
        from amigo.services.screen_pen import emit_tool_anim
        fname_label = os.path.basename(target_path) if target_path else "Documents"
        tool_id = emit_tool_anim("file", label=f"Reading: {fname_label[:20]}", variant="read")
    except Exception as exc:
        logger.debug("[Document QA] Tool anim start skipped: %s", exc)

    evidence, sources = rag_engine.build_document_evidence(
        question,
        top_k=_FILE_RESULT_LIMIT,
        conversation_history=conv_history,
        filepath=target_path if target_path and os.path.exists(target_path) else None,
    )

    if (not evidence or not sources) and target_path:
        evidence, sources = rag_engine.build_document_evidence(
            question,
            top_k=_FILE_RESULT_LIMIT,
            conversation_history=conv_history,
            filepath=None,
        )

    if tool_id:
        try:
            from amigo.services.screen_pen import end_tool_anim
            end_tool_anim(tool_id, is_error=not bool(evidence and sources))
        except Exception as exc:
            logger.debug("[Document QA] Tool anim end skipped: %s", exc)

    if not evidence or not sources:
        return (
            "I couldn't find enough relevant information in your indexed documents.",
            None,
            {"status": "insufficient_evidence"},
        )

    response = get_ai_response(
        question,
        doc_context=evidence,
        conversation_history=conv_history,
        is_voice=False,
        max_tokens=_ANSWER_MAX_TOKENS,
    )
    source_list = "; ".join(f"[{s['id']}] {s['file']} — {s['location']}" for s in sources)
    primary_file = sources[0].get("file", "Document")
    card_sources = [{"file": _as_str(s.get("file")), "location": _as_str(s.get("location"))} for s in sources[:4]]
    emit_roamies_card(
        title=f"Document · {primary_file}",
        heading=question[:36],
        text=response,
        items=[f"{s['file']} ({s['location']})" for s in sources[:4]],
        tone="teal",
        duration=22.0,
        sources=card_sources,
    )
    return f"{response.rstrip()}\n\nSources: {source_list}".strip(), None, {
        "status": "success",
        "doc_context_used": True,
        "sources": sources,
    }


def _tool_summarize_document(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()
    filepath = _as_str(params.get("filepath") or params.get("path") or params.get("filename"))
    t_idx = params.get("target_index")
    target_path = resolve_document_path(filepath, target_index=t_idx) if (filepath or t_idx is not None) else None
    if not target_path:
        act_f = get_active_state(clean_expired=False).get("active_file")
        if isinstance(act_f, dict) and act_f.get("path") and os.path.exists(act_f["path"]):
            target_path = act_f["path"]

    if target_path and os.path.exists(target_path):
        text = rag_engine.extract_text(target_path)
        if text:
            fname = os.path.basename(target_path)
            update_active_state("active_file", {"path": target_path, "name": fname})
            summary = get_ai_response(
                f"Summarize this document clearly with structured bullet points and key takeaways:\n\n{text[:6000]}",
                conversation_history=conv_history,
                use_memory=False,
                is_voice=False,
                max_tokens=_ANSWER_MAX_TOKENS,
            )
            emit_roamies_card(title=f"Summary · {fname}", heading=fname[:36], text=summary, tone="teal", duration=22.0)
            return summary, None, {"status": "completed", "file": target_path}

    return "Which document would you like me to summarize?", None, {"status": "missing_document"}


def _tool_search_knowledge(params, query, spoken):
    conv_history = _get_conv_history()
    q = _as_str((params or {}).get("query") or query)
    ctx = rag_engine.build_rag_context(q, top_k=_FILE_RESULT_LIMIT, conversation_history=conv_history, for_voice=False)
    reply = get_ai_response(q, doc_context=ctx, conversation_history=conv_history, is_voice=False, max_tokens=_ANSWER_MAX_TOKENS)
    return reply, None, {"status": "success"}


def _tool_chat(params, query, spoken, conversation_history=None):
    params = params if isinstance(params, dict) else {}
    doc_context = _as_str(params.get("context") or params.get("doc_context"))
    direct_reply = _as_str(
        spoken
        or params.get("reply")
        or params.get("response")
        or params.get("question")
        or params.get("message")
    )
    conv_history = _get_conv_history(conversation_history)

    is_repetition = False
    if direct_reply and conv_history:
        last_turn = conv_history[-1]
        last_assistant = _as_str(
            last_turn.get("assistant") or last_turn.get("assistant_reply") or last_turn.get("response")
        )
        if last_assistant and len(direct_reply) > 10:
            if direct_reply in last_assistant or last_assistant in direct_reply:
                is_repetition = True

    if direct_reply and not doc_context and not is_repetition:
        if "\n" in direct_reply or len(direct_reply) > 180:
            emit_roamies_card(title="Amigo", heading=(query or "Explanation")[:36], text=direct_reply, tone="teal", duration=20.0)
        return direct_reply, None, {"doc_context_used": False}

    if not _as_str(query) and direct_reply and not is_repetition:
        return direct_reply, None, {"doc_context_used": False}

    mood = _as_str(params.get("mood")).lower()
    if mood:
        try:
            from amigo.services.screen_pen import emit_avatar_act
            emit_avatar_act(mood)
        except Exception as exc:
            logger.debug("[Chat] Avatar act skipped: %s", exc)

    ai_reply = get_ai_response(query, doc_context=doc_context, conversation_history=conv_history)
    if ai_reply and ("\n" in ai_reply or len(ai_reply) > 180):
        emit_roamies_card(title="Amigo", heading=(query or "Explanation")[:36], text=ai_reply, tone="teal", duration=20.0)
    return ai_reply, None, ({"doc_context_used": True} if doc_context else {})


# ── OS & Media Controls ────────────────────────────────────────

def _tool_press_key(params, query, spoken):
    keys = _as_str((params or {}).get("keys"))
    if keys:
        os_automation.press_shortcut(keys)
    return (f"Pressed {keys}." if keys else "Done."), None, {"status": "success"}


def _tool_click_screen(params, query, spoken):
    import pyautogui
    params = params if isinstance(params, dict) else {}
    x, y, button = params.get("x"), params.get("y"), params.get("button", "left")
    try:
        if x is not None and y is not None:
            pyautogui.click(int(x), int(y), button=button)
            return f"Clicked at ({x}, {y}).", None, {"status": "success"}
        pyautogui.click(button=button)
        return "Clicked.", None, {"status": "success"}
    except Exception as e:
        logger.debug("[Click Screen] Failed: %s", e)
        return f"Click error: {e}", None, {"status": "error", "error": str(e)}


_SCREEN_ACT_ACTIONS = {"click": "click", "fill": "fill", "type": "type", "press": "press", "scroll": "scroll"}
_SCROLL_DIRS = {"up": "up", "down": "down", "left": "left", "right": "right"}


def _tool_screen_act(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    app = _as_str(params.get("app"))

    raw_steps = params.get("steps")
    if isinstance(raw_steps, str) and raw_steps.strip().startswith("["):
        try:
            raw_steps = json.loads(raw_steps)
        except Exception as exc:
            logger.debug("[Screen Act] Could not parse steps JSON: %s", exc)

    if isinstance(raw_steps, list) and raw_steps:
        if not app:
            raw_target = _as_str(params.get("target"))
            if raw_target and raw_target.lower() not in ("front", "current", "active", "screen"):
                app = raw_target
        plan_steps = [s for s in raw_steps if isinstance(s, dict)]
    else:
        action = _as_str(params.get("action") or params.get("do")).lower()
        target = _as_str(params.get("target") or params.get("field") or params.get("text"))
        text = _as_str(params.get("text") or params.get("option") or params.get("value"))

        if action in ("click", "click_text"):
            if not target:
                return "Which button or element on screen should I click?", None, {"status": "missing_target"}
            step = {"do": "click", "id": int(target)} if target.isdigit() else {"do": "click_text", "text": target}
        elif action == "fill":
            if not target or not text:
                return "Which field should I fill and what text should I enter?", None, {"status": "missing_params"}
            step = {"do": "fill", "field": target, "text": text}
        elif action in ("type", "type_text"):
            if not text:
                return "What text should I type?", None, {"status": "missing_text"}
            step = {"do": "type", "text": text}
        elif action in ("press", "key", "press_key"):
            keys = text or target
            if not keys:
                return "Which key or shortcut should I press?", None, {"status": "missing_key"}
            step = {"do": "key", "key": keys}
        elif action in ("scroll", "scroll_down", "scroll_up"):
            step = {"do": "scroll", "direction": _SCROLL_DIRS.get(target.lower(), "down")}
        elif action == "choose":
            step = {"do": "choose", "field": target, "option": text}
        elif action == "wait":
            try:
                secs = float(text or target or 1.0)
            except ValueError:
                secs = 1.0
            step = {"do": "wait", "seconds": secs}
        else:
            return "Which on-screen action should I perform: click, fill, type, press, scroll, or choose?", None, {"status": "invalid_action"}
        plan_steps = [step]

    from amigo.services import hands, screen_pen

    def _on_hands_progress(ev):
        bounds = ev.get("bounds") if isinstance(ev, dict) else None
        if ev.get("ev") == "step" and isinstance(bounds, list) and len(bounds) == 4:
            screen_pen.push_overlay_event({
                "type": "ink",
                "id": f"hands-step-{int(time.time() * 1000)}",
                "seg": 0,
                "last": True,
                "fade": 2.0,
                "beats": [{"marks": [{"kind": "ring", "part": "p1", "at": {"rect": bounds}, "tone": "teal"}]}],
            })

    plan: dict[str, Any] = {"steps": plan_steps}
    if app and app.lower() not in ("front", "current", "active", "screen"):
        plan["app"] = app
    res = hands.act(plan, on_progress=_on_hands_progress)
    if res.get("ok"):
        done = res.get("done")
        summary = ", ".join(done) if isinstance(done, list) and done else "Action completed"
        return f"{summary}.", None, {"hands_used": True, "plan": plan}
    return _as_str(res.get("error"), "Could not complete on-screen action."), None, {"hands_used": True, "error": res.get("error")}


_audio_playing_cache = {"status": False, "time": 0.0}
_audio_cache_lock = threading.Lock()


def _is_system_audio_playing() -> bool:
    now = time.time()
    with _audio_cache_lock:
        if (now - _audio_playing_cache["time"]) < _AUDIO_CACHE_TTL_SECONDS:
            return bool(_audio_playing_cache["status"])

    status = False
    com_initialized = False
    try:
        import pythoncom
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        hr = pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        com_initialized = hr in (0, 1)
        sessions = None
        try:
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                if s.Process:
                    meter = None
                    try:
                        meter = s._ctl.QueryInterface(IAudioMeterInformation)
                        if meter.GetPeakValue() > 0.0005:
                            status = True
                            break
                    except Exception:
                        pass
                    finally:
                        if meter is not None:
                            del meter
        finally:
            if sessions is not None:
                del sessions
            if com_initialized:
                pythoncom.CoUninitialize()
    except Exception as exc:
        logger.debug("[Audio Check] pycaw session check failed: %s", exc)

    with _audio_cache_lock:
        _audio_playing_cache["status"] = status
        _audio_playing_cache["time"] = now
    return status


_live_media_cache: tuple[str, str, str] | None = None
_live_media_cache_time: float = 0.0
_live_media_refreshing: bool = False
_live_media_lock = threading.Lock()


def _fetch_live_media_worker() -> None:
    global _live_media_cache, _live_media_cache_time, _live_media_refreshing
    try:
        res = None
        try:
            sys_media = os_automation.get_system_media_info()
            if sys_media and sys_media.get("title"):
                status = sys_media.get("status", "playing")
                if status.lower() not in ("closed", "stopped"):
                    res = (sys_media["title"], sys_media.get("artist", ""), status)
        except Exception as exc:
            logger.debug("[Live Media Worker] get_system_media_info failed: %s", exc)
        with _live_media_lock:
            _live_media_cache = res
            _live_media_cache_time = time.time()
    finally:
        with _live_media_lock:
            _live_media_refreshing = False


def _get_live_media_title_from_windows(wait_if_cold: bool = False) -> tuple[str, str, str] | None:
    global _live_media_refreshing
    now = time.time()
    with _live_media_lock:
        if (now - _live_media_cache_time) < _MEDIA_CACHE_TTL_SECONDS and _live_media_cache is not None:
            return _live_media_cache
        if not _live_media_refreshing:
            _live_media_refreshing = True
            _IO_EXECUTOR.submit(_fetch_live_media_worker)
        current_cache = _live_media_cache

    if current_cache is None and wait_if_cold:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with _live_media_lock:
                if not _live_media_refreshing or _live_media_cache is not None:
                    break
            time.sleep(0.02)
    with _live_media_lock:
        return _live_media_cache


def _tool_stop(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception as exc:
        logger.debug("[Stop Tool] stop_speaking failed: %s", exc)
    if _is_system_audio_playing():
        try:
            os_automation.play_pause_media()
        except Exception as exc:
            logger.debug("[Stop Tool] play_pause_media failed: %s", exc)
    return "", None, {"status": "stopped"}


def _tool_stop_speaking(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception as exc:
        logger.debug("[Stop Speaking] stop_speaking failed: %s", exc)
    return "", None, {"status": "stopped"}


def _tool_blocked(params, query, spoken):
    return "", None, {"status": "blocked"}


def _tool_pause_media(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception as exc:
        logger.debug("[Pause Media] stop_speaking failed: %s", exc)
    live_media = _get_live_media_title_from_windows()
    if live_media and live_media[2].lower() == "paused":
        return "Media is already paused.", None, {"status": "already_paused"}
    try:
        os_automation.play_pause_media()
    except Exception as exc:
        logger.debug("[Pause Media] play_pause_media failed: %s", exc)
    _notify_media_update({"status": "paused"})
    media = get_active_state(clean_expired=True).get("current_media")
    if isinstance(media, dict):
        update_active_state("current_media", {**media, "status": "paused"})
    return "Media paused.", None, {"status": "paused"}


def _tool_play_media(params, query, spoken):
    live_media = _get_live_media_title_from_windows()
    if live_media and live_media[2].lower() == "playing":
        return "Media is already playing.", None, {"status": "already_playing"}
    try:
        os_automation.play_pause_media()
    except Exception as exc:
        logger.debug("[Play Media] play_pause_media failed: %s", exc)
    _notify_media_update({"status": "playing"})
    media = get_active_state(clean_expired=False).get("current_media")
    if isinstance(media, dict):
        update_active_state("current_media", {**media, "status": "playing"})
    return "Media resumed.", None, {"status": "playing"}


def _mark_media_stale():
    media = get_active_state(clean_expired=False).get("current_media")
    if isinstance(media, dict) and media:
        update_active_state("current_media", {**media, "stale": True, "status": "playing"})


def _tool_next_track(params, query, spoken):
    os_automation.next_track()
    _mark_media_stale()
    return "Next track.", None, {"status": "next"}


def _tool_prev_track(params, query, spoken):
    os_automation.prev_track()
    _mark_media_stale()
    return "Previous track.", None, {"status": "previous"}


def _tool_media_control(params, query, spoken):
    action = _as_str((params or {}).get("action")).lower()
    target = _MEDIA_ACTION_MAP.get(action)
    if target and target in UI_TOOL_HANDLERS:
        return UI_TOOL_HANDLERS[target](params, query, spoken)
    return "Which media action would you like: play, pause, next, previous, or current status?", None, {"status": "missing_action"}


def get_pending_confirmation() -> dict[str, Any] | None:
    global _PENDING_CONFIRMATION
    with _state_lock:
        if _PENDING_CONFIRMATION:
            start_time = _PENDING_CONFIRMATION.get("timer_started") or _PENDING_CONFIRMATION.get("created")
            if start_time and (time.time() - start_time) > _PENDING_TTL_SECONDS:
                _PENDING_CONFIRMATION = None
        return dict(_PENDING_CONFIRMATION) if _PENDING_CONFIRMATION else None


def start_pending_confirmation_timer() -> None:
    with _state_lock:
        if _PENDING_CONFIRMATION:
            _PENDING_CONFIRMATION["timer_started"] = time.time()


def _pending_confirmation_line() -> str:
    pending = get_pending_confirmation()
    if not pending:
        return ""
    action_name = pending.get("action") or pending.get("tool") or "action"
    return (
        f"[Awaiting user confirmation: user was asked to confirm '{action_name}'. "
        "Use 'confirm_action' ONLY if user affirmatively agrees. If the user issues a new command, route to that tool directly.]"
    )


def _require_confirmation(action_name: str, params: dict, query: str, spoken: str, tool: str | None = None) -> tuple[str, None, dict]:
    global _PENDING_CONFIRMATION
    if isinstance(params, dict) and params.get("confirmed") is True:
        return "", None, {}
    call = getattr(_call_ctx, "value", None) or {}
    tool_name = tool or call.get("tool")
    clean_params = {k: v for k, v in params.items() if k != "confirmed"} if isinstance(params, dict) else {}
    action_text = action_name[:1].lower() + action_name[1:]
    if tool_name:
        with _state_lock:
            _PENDING_CONFIRMATION = {
                "tool": tool_name,
                "params": clean_params,
                "query": call.get("query", query),
                "action": action_text,
                "created": time.time(),
                "timer_started": None,
                "remaining_actions": [],
            }
    return f"Do you want me to {action_text}? Say yes to go ahead.", None, {
        "status": "requires_confirmation",
        "requires_confirmation": True,
        "action": action_name,
        "original_params": params,
        "original_query": query,
        "original_spoken": spoken,
    }


def _tool_close_app(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    name = _as_str(params.get("name") or params.get("app_name") or params.get("app"))
    if not name:
        os_automation.window_action("close_window")
        return "Window closed.", None, {"status": "closed"}
    closed = os_automation.close_app(name)
    return (
        (f"Closed {name.title()}." if closed else f"{name.title()} is not currently running."),
        None,
        {"status": "closed" if closed else "not_running"},
    )


def _tool_window_management(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = params.get("action", "")
    app_name = params.get("app_name", "") or params.get("name", "")
    if action in ("close_window", "close_app"):
        return _tool_close_app({"app_name": app_name}, query, spoken)
    if action:
        os_automation.window_action(action)
    return "Done.", None, {"status": "success"}


def _tool_calculate(params, query, spoken):
    expr = _as_str((params or {}).get("expression") or query)
    if expr:
        try:
            res = Calc(expr)
            if res is not None:
                return f"The answer is {res}.", None, {"status": "success", "result": res}
        except Exception as exc:
            logger.debug("[Calculate] Calc failed for '%s': %s", expr, exc)
    return get_ai_response(f"Solve this concisely: {expr or query}", use_memory=False), None, {"status": "llm_solved"}


def _tool_clarification(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    candidates = params.get("candidate_tools", [])
    q = params.get("query", query)
    custom_msg = params.get("message")
    spoken_reply = custom_msg or f"I'm not completely sure what you'd like to do with '{q}'. Please clarify."
    return spoken_reply, None, {
        "status": "requires_clarification",
        "requires_clarification": True,
        "candidates": candidates,
        "query": q,
    }


_SIMPLE_OS_ACTIONS = {
    "scroll_down":     (os_automation.scroll_down, ""),
    "scroll_up":       (os_automation.scroll_up, ""),
    "close_tab":       (os_automation.close_tab, "Closing tab."),
    "next_tab":        (os_automation.next_tab, ""),
    "prev_tab":        (os_automation.prev_tab, ""),
    "volume_up":       (os_automation.volume_up, "Volume increased."),
    "volume_down":     (os_automation.volume_down, "Volume decreased."),
    "mute":            (os_automation.mute, "Audio muted."),
    "cancel_shutdown": (os_automation.cancel_shutdown, "Shutdown cancelled."),
}


def _make_simple_handler(func, default_msg):
    def _handler(params, query, spoken):
        func()
        return default_msg, None, {"status": "success"}
    return _handler


def _tool_empty_recycle_bin(params, query, spoken):
    confirm = _require_confirmation("Empty recycle bin", params, query, "Emptying recycle bin")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.empty_recycle_bin()
    return "Recycle bin emptied.", None, {"status": "success"}


def _tool_lock_pc(params, query, spoken):
    confirm = _require_confirmation("Lock PC", params, query, "Locking PC")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.lock_pc()
    return "Locking your PC.", None, {"status": "success"}


def _tool_sleep_pc(params, query, spoken):
    confirm = _require_confirmation("Sleep PC", params, query, "Putting system to sleep")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.sleep_pc()
    return "Putting system to sleep.", None, {"status": "success"}


def _tool_restart_pc(params, query, spoken):
    confirm = _require_confirmation(f"Restart PC in {_POWER_GRACE_SECONDS} seconds", params, query, "Restarting PC")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.restart_pc(_POWER_GRACE_SECONDS)
    return f"Restarting in {_POWER_GRACE_SECONDS} seconds.", None, {"status": "success"}


def _tool_system_control(params, query, spoken):
    action = _as_str((params or {}).get("action")).lower()
    target = _POWER_ACTION_MAP.get(action)
    if target and target != "system_control" and target in UI_TOOL_HANDLERS:
        return UI_TOOL_HANDLERS[target](params, query, spoken)
    if action in ("shutdown", "shutdown_pc"):
        confirm = _require_confirmation(f"Shutdown PC in {_POWER_GRACE_SECONDS} seconds", params, query, "Shutting down PC")
        if confirm[2].get("requires_confirmation"):
            return confirm
        shutdown_fn = getattr(os_automation, "shutdown_pc", None)
        if callable(shutdown_fn):
            shutdown_fn(_POWER_GRACE_SECONDS)
        else:
            subprocess.Popen(["shutdown", "/s", "/t", str(_POWER_GRACE_SECONDS)])
        return f"Shutting down in {_POWER_GRACE_SECONDS} seconds.", None, {"status": "success"}
    if action == "cancel_shutdown":
        os_automation.cancel_shutdown()
        return "Shutdown cancelled.", None, {"status": "success"}
    if action in ("empty_recycle_bin", "empty_bin", "recycle_bin"):
        return _tool_empty_recycle_bin(params, query, "Emptying recycle bin")
    return f"Unknown system control action: {action}", None, {"status": "unknown_action"}


def _tool_file_action(params, query, spoken, default_action="open"):
    params = params if isinstance(params, dict) else {}
    target = _as_str(params.get("path") or params.get("name"))
    action = params.get("action") or default_action
    t_idx = params.get("target_index")

    if t_idx is None and target and not os.path.isfile(target):
        matches, _ = find_files(target)
        if len(matches) > 1 and os.path.basename(matches[0]).lower() != target.lower():
            update_active_state("found_files", matches[:_FILE_RESULT_LIMIT])
            lines = [f"{i}. {os.path.basename(p)}" for i, p in enumerate(matches[:_FILE_RESULT_LIMIT], 1)]
            return (
                "I found multiple matching files:\n" + "\n".join(lines) + "\nWhich one would you like to open?",
                None,
                {"status": "multiple_matches", "matches": matches[:_FILE_RESULT_LIMIT]},
            )

    target_path = resolve_document_path(target, target_index=t_idx)
    if not (target_path and os.path.exists(target_path)):
        return "Which file would you like to open?", None, {"status": "missing_file"}

    update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
    ok, msg = execute_file_action(target_path, action)
    if not ok:
        return msg or f"I couldn't {str(action).replace('_', ' ')} that file.", None, {"status": "error"}
    clean_name = os.path.splitext(os.path.basename(target_path))[0].replace("_", " ").replace("-", " ").title()
    return (f"Opened {clean_name}." if action == "open" else msg or f"Done with {clean_name}."), None, {"status": "success", "path": target_path}


def _make_file_handler(default_action):
    def _handler(params, query, spoken):
        return _tool_file_action(params, query, spoken, default_action=default_action)
    return _handler


def _tool_show_images(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    target = _as_str(params.get("query") or query)
    if not target:
        return spoken or "What would you like to see images of?", None, {"status": "missing_query"}

    from amigo.services.web_search import search_images
    from amigo.services.screen_pen import emit_roamies_images

    img_future = _IO_EXECUTOR.submit(search_images, target, 4)
    exp_future = _IO_EXECUTOR.submit(
        get_ai_response,
        f"Provide a crisp, direct 1 to 2 sentence visual description and explanation of {target}.",
        max_tokens=_VISUAL_SUMMARY_MAX_TOKENS,
        is_voice=True,
    )

    try:
        images = img_future.result(timeout=8.0) or []
    except Exception as exc:
        logger.debug("[Show Images] search_images failed: %s", exc)
        images = []

    explanation = ""
    try:
        explanation = _as_str(exp_future.result(timeout=8.0))
    except Exception as exc:
        logger.debug("[Show Images] Explanation generation note: %s", exc)

    primary_url = images[0]["source_url"] if images else f"https://www.google.com/search?q={urllib.parse.quote(target)}"

    if images:
        bullets = [f"{im['title'][:40]} ({im['domain']})" for im in images[:4] if im.get("title") and im.get("domain")]
        emit_roamies_images(
            title=f"Images · {target[:28]}",
            images=images,
            summary=explanation or f"Visual results for {target}.",
            bullets=bullets,
            duration=26.0,
        )
        speech = spoken or (explanation if explanation else f"Here are image results for {target}.")
    else:
        desc = explanation or f"No direct web images found for {target}."
        emit_roamies_card(
            title=f"Images · {target[:28]}",
            heading=target[:36],
            text=desc,
            items=[f"Query: {target}", "Visual summary displayed"],
            tone="teal",
            duration=22.0,
        )
        speech = spoken or (explanation if explanation else f"I couldn't find web photos for {target}, but here is a visual overview: {desc}")

    return speech, primary_url, {
        "status": "success",
        "query": target,
        "images": images,
        "count": len(images),
    }


def _tool_search_and_type(params, query, spoken):
    confirm = _require_confirmation("Search and type", params, query, spoken or "Searching and typing")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.search_and_type(_as_str((params or {}).get("text")))
    return spoken, None, {"status": "success"}


def _tool_new_tab(params, query, spoken):
    os_automation.new_tab(_as_str((params or {}).get("url")))
    return spoken, None, {"status": "success"}


def _level_result(res, default_msg):
    if isinstance(res, (tuple, list)):
        return res[-1]
    return res if isinstance(res, str) else default_msg


def _tool_set_volume(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = _as_str(params.get("action")).lower()
    level = params.get("level")

    if level is not None:
        try:
            val = int(level)
            if 0 <= val <= 100:
                os_automation.set_volume(val)
                actual = os_automation.get_volume()
                final_val = actual if actual is not None else val
                emit_interactive_volume(final_val)
                return f"Volume set to {final_val} percent.", None, {"status": "success", "volume": final_val}
            return "What volume level would you like, from 0 to 100?", None, {"status": "invalid_level"}
        except (ValueError, TypeError):
            if action == "set_volume":
                return "What volume level would you like, from 0 to 100?", None, {"status": "invalid_level"}

    if action == "mute":
        os_automation.mute()
        emit_interactive_volume(0)
        return "Audio muted.", None, {"status": "muted"}
    if action == "unmute":
        unmute_fn = getattr(os_automation, "unmute", None)
        if callable(unmute_fn):
            unmute_fn()
        else:
            current_vol = os_automation.get_volume()
            os_automation.set_volume(current_vol if (current_vol is not None and current_vol > 0) else 50)
        actual = os_automation.get_volume()
        final_val = actual if actual is not None else 50
        emit_interactive_volume(final_val)
        return "Audio unmuted.", None, {"status": "unmuted", "volume": final_val}
    if action == "volume_up":
        os_automation.volume_up()
        actual = os_automation.get_volume()
        emit_interactive_volume(actual if actual is not None else 65)
        return "Volume increased.", None, {"status": "volume_up"}
    if action == "volume_down":
        os_automation.volume_down()
        actual = os_automation.get_volume()
        emit_interactive_volume(actual if actual is not None else 45)
        return "Volume decreased.", None, {"status": "volume_down"}
    return "What volume level would you like, from 0 to 100?", None, {"status": "missing_level"}


def _tool_set_brightness(params, query, spoken):
    level = (params or {}).get("level")
    try:
        val = int(level)
        if not (0 <= val <= 100):
            return "What brightness level would you like, from 0 to 100?", None, {"status": "invalid_level"}
        level = val
    except (ValueError, TypeError):
        return "What brightness level would you like, from 0 to 100?", None, {"status": "missing_level"}
    emit_interactive_brightness(level)
    return _level_result(os_automation.set_brightness(level), f"Brightness set to {level} percent."), None, {"status": "success", "brightness": level}


def _tool_open_settings(params, query, spoken):
    s = _as_str((params or {}).get("setting") or (params or {}).get("name")).lower()
    if s in ("close", "hide", "dismiss"):
        _broadcast_ui("close_settings", {})
        return "Closed settings.", None, {"status": "closed"}
    _broadcast_ui("open_settings", {"setting": s})
    return open_setting(s or "home"), None, {"status": "opened"}


def _tool_control_windows_system(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    category = _as_str(params.get("category") or params.get("action")).lower()
    try:
        from amigo.services.os_automation import control_windows_system
        res = control_windows_system(
            category=category,
            enabled=params.get("enabled"),
            mode=params.get("mode"),
            level=params.get("level"),
        )
        if isinstance(res, tuple):
            return (res[1] or "Which system power setting would you like to adjust?"), None, {"status": "missing_param"}
        msg = res.get("message")
        if not msg:
            if res.get("status") in ("success", "already_connected", "already_on", "already_off"):
                msg = f"{category.capitalize()} status updated successfully."
            else:
                msg = res.get("details") or res.get("code") or f"Could not adjust {category}."
        return msg, None, res
    except Exception as exc:
        logger.error("[Control Windows System Error]: %s", exc)
        return f"Could not adjust system {category}: {exc}", None, {"status": "error", "error": str(exc)}


def _tool_screen_pen_guide(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    from amigo.services.screen_pen import screen_pen_guide
    duration = float(params.get("duration", 18.0))
    res = screen_pen_guide(
        steps=params.get("steps") or [],
        topic=params.get("topic") or query,
        app=params.get("app"),
        duration=duration,
    )
    msg = (
        f"Showing a {res.get('step_count', 0)}-step visual guide on screen."
        if res.get("status") == "success"
        else _as_str(res.get("error"), "Could not draw guide.")
    )
    return msg, None, res


def _tool_screen_pen_annotate(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = _as_str(params.get("action")).lower()
    label = params.get("label")
    topic = _as_str(params.get("topic"))
    beats = params.get("beats")
    data = params.get("data")
    color = params.get("color", "teal")
    duration = float(params.get("duration", 12.0))
    normalized = bool(params.get("normalized", True))

    if not action:
        action = "circle" if label else ("diagram" if (topic or data or beats) else "circle")
    if not topic and not label and query:
        topic = str(query)

    try:
        from amigo.services.screen_pen import screen_pen_annotate, screen_pen_guide
        if action == "guide" or "steps" in params:
            res = screen_pen_guide(
                steps=params.get("steps") or ([label] if label else []),
                topic=params.get("topic") or topic or query,
                app=params.get("app"),
                duration=duration,
            )
            msg = (
                f"Showing a {res.get('step_count', 0)}-step visual guide on screen."
                if res.get("status") == "success"
                else _as_str(res.get("error"), "Could not draw guide.")
            )
            return msg, None, res

        res = screen_pen_annotate(
            action=action,
            x=params.get("x"),
            y=params.get("y"),
            x2=params.get("x2"),
            y2=params.get("y2"),
            label=label,
            color=color,
            duration=duration,
            normalized=normalized,
            topic=topic,
            data=data,
            beats=beats,
        )
        if res.get("status") == "success":
            msg = res.get("summary") or (
                f"Marked '{label}' on screen." if label else f"Highlighted on screen with {res.get('drawn', action)}."
            )
        else:
            msg = _as_str(
                res.get("error"),
                f"Could not find '{label}' on screen." if label else f"Screen pen action {action} could not be completed.",
            )
        return msg, None, res
    except Exception as exc:
        logger.error("[Screen Pen Annotate Error]: %s", exc)
        return f"Could not annotate screen: {exc}", None, {"status": "error", "error": str(exc)}


def _tool_change_setting(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    s = _as_str(params.get("setting"))
    if not s:
        return "Which setting would you like to change?", None, {"status": "missing_setting"}
    val = params.get("value")
    confirmed = bool(params.get("confirmed", False))

    try:
        from amigo.services.os_settings import change_setting, get_setting_state
        ok, msg = change_setting(s, value=val, confirmed=confirmed)
        if not ok and not confirmed:
            return msg, None, {"requires_confirmation": True, "action_type": "change_setting", "setting": s, "value": val}
        _, is_on, _ = get_setting_state(s)
        emit_interactive_setting(s, msg, bool(is_on))
        return msg, None, {"status": "success", "setting": s, "is_on": bool(is_on)}
    except Exception as exc:
        logger.error("[Change Setting Error]: %s", exc)
        return f"Could not change setting: {exc}", None, {"status": "error", "error": str(exc)}


def _tool_get_setting(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    s = _as_str(params.get("setting"))
    if not s:
        return "Which setting would you like to check?", None, {"status": "missing_setting"}
    try:
        from amigo.services.os_settings import get_setting_state
        _, is_on, msg = get_setting_state(s)
        emit_interactive_setting(s, msg, bool(is_on))
        return msg, None, {"status": "success", "setting": s, "is_on": bool(is_on)}
    except Exception as exc:
        logger.error("[Get Setting Error]: %s", exc)
        return f"Could not read setting: {exc}", None, {"status": "error", "error": str(exc)}


def _tool_open_history(params, query, spoken):
    action = _as_str((params or {}).get("action") or "open").lower()
    if action in ("close", "hide"):
        _broadcast_ui("close_history", {})
        return "Closed history.", None, {"status": "closed"}
    _broadcast_ui("open_history", {})
    return "Opened history.", None, {"status": "opened"}


def _tool_system_status(params, query, spoken):
    return os_automation.get_system_status().get("summary", "System status checked."), None, {"status": "success"}


def _tool_set_timer(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    msg = handle_set_timer(params, query)
    parsed_secs = (
        parse_relative_seconds(_as_str(params.get("query")))
        or parse_relative_seconds(query or "")
        or parse_relative_seconds(_as_str(params.get("duration") or params.get("seconds")))
        or 0
    )
    label = _as_str(params.get("label") or "Timer")
    mins, secs = int(parsed_secs // 60), int(parsed_secs % 60)
    timer_items = [f"Duration: {mins}m {secs}s", f"Label: {label}"] if parsed_secs else [msg]
    emit_roamies_card(
        title=f"Timer · {label}",
        heading=f"{mins}m {secs}s Countdown" if parsed_secs else "Timer Started",
        text=msg,
        items=timer_items,
        tone="teal",
        duration=18.0,
    )
    try:
        from amigo.services.screen_pen import emit_avatar_act, emit_overlay_sfx
        emit_overlay_sfx("pop")
        emit_avatar_act("remind")
    except Exception as exc:
        logger.debug("[Set Timer] Avatar/SFX skipped: %s", exc)
    return msg, None, {"status": "success", "seconds": parsed_secs}


def _tool_set_reminder(params, query, spoken):
    msg = handle_set_reminder(params, query)
    reminders = get_active_data().get("reminders", [])
    emit_interactive_reminders(reminders)
    return msg, None, {"cards": [], "reminders": reminders}


def _tool_list_reminders(params, query, spoken):
    msg = handle_list_reminders()
    reminders = get_active_data().get("reminders", [])
    emit_interactive_reminders(reminders)
    return msg, None, {"cards": [], "reminders": reminders}


def _tool_cancel_reminder(params, query, spoken):
    res_msg = handle_cancel_reminder(params)
    emit_roamies_card(title="Reminder Update", heading="Reminder Cancelled", text=res_msg, tone="coral", duration=14.0)
    return res_msg, None, {"status": "cancelled"}


def _tool_snooze_reminder(params, query, spoken):
    dur_secs = 600.0
    if isinstance(params, dict):
        if "duration_seconds" in params:
            try:
                dur_secs = float(params["duration_seconds"])
            except (ValueError, TypeError):
                pass
        elif "duration" in params:
            parsed = parse_relative_seconds(_as_str(params["duration"]))
            if parsed:
                dur_secs = parsed
    msg = handle_snooze_reminder(params, duration_seconds=dur_secs)
    reminders = get_active_data().get("reminders", [])
    emit_interactive_reminders(reminders)
    emit_roamies_card(title="Reminder Snoozed", heading="Snoozed", text=msg, tone="sky", duration=14.0)
    return msg, None, {"cards": [], "reminders": reminders}


def _tool_start_focus_mode(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    minutes = int(params.get("duration_minutes") or 30)
    goal = _as_str(params.get("goal") or params.get("task") or "Deep Work")
    from amigo.core.proactive_intelligence import start_focus_session
    msg = start_focus_session(duration_minutes=minutes, goal=goal)
    emit_roamies_card(
        title="Focus Mode",
        heading=f"Focus: {goal}"[:36],
        text=f"Focus session active for {minutes} minutes.",
        items=[f"Goal: {goal}", f"Duration: {minutes} minutes", "Notifications quieted"],
        tone="teal",
        duration=20.0,
    )
    return msg, None, {"cards": []}


def _tool_morning_briefing(params, query, spoken):
    from amigo.core.proactive_intelligence import generate_morning_briefing
    summary, items = generate_morning_briefing()
    emit_roamies_card(title="Morning Briefing", heading="Today's Overview", text=summary, items=items, tone="amber", duration=25.0)
    return summary, None, {"cards": []}


def _tool_exit(params, query, spoken):
    return "Goodbye!", None, {"status": "exit"}


# ── Email & Calendar Handlers ──────────────────────────────────

def _tool_read_emails(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = _as_str(params.get("action")).lower()
    if action == "unread":
        return _tool_unread_emails(params, query, spoken)
    if action == "search" or (not action and params.get("query")):
        return _tool_search_emails(params, query, spoken)
    try:
        from amigo.services.mail_integration import get_email_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available.", None, {"status": "unavailable"}
        summary = get_email_summary_text(int(params.get("count") or _FILE_RESULT_LIMIT))
        return get_ai_response(f"Summarize these emails naturally:\n{summary}", use_memory=False), None, {"status": "success"}
    except Exception as exc:
        logger.debug("[Read Emails] Failed: %s", exc)
        return "Could not read emails.", None, {"status": "error"}


def _tool_search_emails(params, query, spoken):
    conv_history = _get_conv_history()
    q = _as_str((params or {}).get("query") or query)
    try:
        from amigo.services.mail_integration import is_outlook_available, search_emails
        if is_outlook_available():
            results = search_emails(q, max_results=_FILE_RESULT_LIMIT)
            if results:
                lines = [f"{i}. From: {e['sender']} | Subject: {e['subject']}" for i, e in enumerate(results, 1)]
                return f"Found {len(results)} email(s) matching '{q}':\n" + "\n".join(lines), None, {"status": "success", "count": len(results)}
    except Exception as exc:
        logger.debug("[Search Emails] Outlook search failed: %s", exc)
    rag_ctx = rag_engine.build_rag_context(query or q, top_k=_FILE_RESULT_LIMIT, conversation_history=conv_history)
    if rag_ctx:
        return get_ai_response(query or q, doc_context=rag_ctx, conversation_history=conv_history), None, {"status": "rag_fallback"}
    return f"No emails found matching '{q}'.", None, {"status": "no_results"}


def _tool_unread_emails(params, query, spoken):
    try:
        from amigo.services.mail_integration import get_unread_count, get_unread_emails, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available.", None, {"status": "unavailable"}
        count = get_unread_count()
        if count <= 0:
            return "You have no unread emails.", None, {"status": "success", "count": 0}
        emails = get_unread_emails(min(count, _FILE_RESULT_LIMIT))
        lines = [f"You have {count} unread email{'s' if count > 1 else ''}."]
        lines.extend(f"  {i}. From {e['sender']}: {e['subject']}" for i, e in enumerate(emails, 1))
        return "\n".join(lines), None, {"status": "success", "count": count}
    except Exception as exc:
        logger.debug("[Unread Emails] Failed: %s", exc)
        return "Could not check unread emails.", None, {"status": "error"}


def _tool_draft_email(params, query, spoken):
    try:
        from amigo.services.mail_integration import draft_email
        params = params if isinstance(params, dict) else {}
        to = _as_str(params.get("to"))
        subject = _as_str(params.get("subject"))
        prompt = _as_str(params.get("prompt") or query)
        body = get_ai_response(
            f"Write a professional email. Context: {prompt}. To: {to or 'recipient'}. Subject: {subject or 'as appropriate'}. Write ONLY the email body.",
            use_memory=False,
        )
        return draft_email(to=to, subject=subject, body=body), None, {"status": "drafted"}
    except Exception as exc:
        logger.debug("[Draft Email] Failed: %s", exc)
        return "Could not create email draft.", None, {"status": "error"}


def _tool_get_calendar(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    if params.get("query"):
        return _tool_search_calendar(params, query, spoken)
    try:
        from amigo.services.calendar_integration import get_calendar_summary_text, get_upcoming_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None, {"status": "unavailable"}
        raw_days = params.get("days")
        try:
            days = int(raw_days) if raw_days is not None else 1
        except (ValueError, TypeError):
            days = 1
        return (get_calendar_summary_text() if days <= 1 else get_upcoming_summary_text(days=days)), None, {"status": "success"}
    except Exception as exc:
        logger.debug("[Get Calendar] Failed: %s", exc)
        return "Could not read calendar.", None, {"status": "error"}


def _tool_search_calendar(params, query, spoken):
    try:
        from amigo.services.calendar_integration import is_outlook_available, search_events
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None, {"status": "unavailable"}
        q = _as_str((params or {}).get("query") or query)
        results = search_events(q, days=30)
        if results:
            lines = [f"Found {len(results)} event(s) matching '{q}':"]
            for i, e in enumerate(results, 1):
                loc = f" at {e['location']}" if e.get("location") else ""
                lines.append(f"  {i}. {e['subject']}{loc} - {e['start']}")
            return "\n".join(lines), None, {"status": "success", "count": len(results)}
        return f"No calendar events found matching '{q}'.", None, {"status": "no_results"}
    except Exception as exc:
        logger.debug("[Search Calendar] Failed: %s", exc)
        return "Could not search calendar.", None, {"status": "error"}


def _tool_confirm_action(params, query, spoken):
    global _PENDING_CONFIRMATION
    with _state_lock:
        pending = get_pending_confirmation()
        if not pending:
            return "There's nothing waiting for confirmation right now.", None, {"status": "no_pending"}

        params = params if isinstance(params, dict) else {}
        target_action = _as_str(params.get("action") or params.get("tool")).lower()
        if target_action:
            pending_tool = str(pending.get("tool") or "").lower()
            pending_action = str(pending.get("action") or "").lower()
            matched = (
                target_action in pending_tool
                or target_action in pending_action
                or pending_tool in target_action
                or pending_action in target_action
            )
            if not matched:
                return (
                    f"Cannot confirm: '{target_action}' does not match the pending action '{pending_action}'.",
                    None,
                    {"status": "action_mismatch", "error": "confirmation_action_mismatch"},
                )

        remaining_actions = list(pending.get("remaining_actions") or [])
        _PENDING_CONFIRMATION = None

    first_res = execute_tool(pending["tool"], {**pending["params"], "confirmed": True}, query=pending["query"], spoken="")
    if remaining_actions:
        rest_results = execute_actions(remaining_actions, query=pending["query"])
        all_spoken = [first_res[0]] + [r[0] for r in rest_results if r[0]]
        last_url = next((r[1] for r in reversed(rest_results) if r[1]), first_res[1])
        merged_meta = {**(first_res[2] or {}), "multi_step_resumed": True}
        return " ".join(s for s in all_spoken if s).strip(), last_url, merged_meta
    return first_res


def _tool_cancel_action(params, query, spoken):
    global _PENDING_CONFIRMATION
    with _state_lock:
        had_pending = get_pending_confirmation() is not None
        _PENDING_CONFIRMATION = None
    return ("Okay, I won't do that." if had_pending else "Nothing to cancel."), None, {"status": "cancelled" if had_pending else "no_pending"}


def _tool_get_clipboard(params, query, spoken):
    from amigo.core.llm_agent import get_clipboard_text
    clip = get_clipboard_text()
    if clip:
        return f"Clipboard content:\n{clip}", None, {"clipboard": clip}
    return "The clipboard is currently empty.", None, {"clipboard": ""}


def _tool_manage_voices(params, query, spoken):
    from amigo.utils.tts import get_active_voice, get_voice_catalog, set_voice

    params = params if isinstance(params, dict) else {}
    action = _as_str(params.get("action") or "list").lower()
    voice_param = _as_str(params.get("voice")).lower()
    gender_param = _as_str(params.get("gender")).lower()
    catalog = get_voice_catalog() or {}
    active_key = get_active_voice()
    active_name = (catalog.get(active_key) or {}).get("name", active_key)

    if action == "set" or voice_param or gender_param:
        catalog_genders = {v.get("gender", "").lower() for v in catalog.values() if v.get("gender")}
        if not gender_param and voice_param in catalog_genders:
            gender_param = voice_param
            voice_param = ""
        chosen_key = None
        if gender_param:
            chosen_key = next(
                (k for k, v in catalog.items() if v.get("gender", "").lower() == gender_param and k != active_key),
                None,
            ) or next(
                (k for k, v in catalog.items() if v.get("gender", "").lower() == gender_param),
                None,
            )
        elif voice_param:
            for k, v in catalog.items():
                name = v.get("name", "").lower()
                if voice_param in (k.lower(), name) or voice_param in name or voice_param in k.lower():
                    chosen_key = k
                    break

        if chosen_key:
            display_name = set_voice(chosen_key)
            return f"Switched voice to {display_name}.", None, {"action": "set", "voice": chosen_key, "display_name": display_name}
        return f"I couldn't find a voice matching '{voice_param or gender_param}'.", None, {"action": "set", "error": "voice_not_found"}

    total_voices = len(catalog)
    seen_names: list[str] = []
    for k, v in catalog.items():
        v_name = v.get("name", k)
        if v_name not in seen_names:
            seen_names.append(v_name)
        if len(seen_names) >= 5:
            break

    if len(seen_names) > 1:
        spoken_summary = (
            f"I have {total_voices} voices available. Currently using {active_name}. "
            f"Options include {', '.join(seen_names[:-1])}, and {seen_names[-1]}."
        )
    elif len(seen_names) == 1:
        spoken_summary = f"I have {total_voices} voice available: {seen_names[0]}."
    else:
        spoken_summary = f"Currently using {active_name}."

    return spoken_summary, None, {
        "action": "list",
        "active_voice": active_key,
        "voices": catalog,
        "cards": [{
            "type": "voice_list",
            "active": active_key,
            "total": total_voices,
            "voices": [
                {"id": k, "name": v.get("name", k), "gender": v.get("gender"), "accent": v.get("accent")}
                for k, v in catalog.items()
            ],
        }],
    }


UI_TOOL_HANDLERS = {
    "manage_voices":          _tool_manage_voices,
    "get_clipboard":          _tool_get_clipboard,
    "get_clipboard_content":  _tool_get_clipboard,
    "web_search":             _tool_web_search,
    "open_website":           _tool_open_website,
    "show_images":            _tool_show_images,
    "play_youtube":           _tool_play_youtube,
    "get_time":               _tool_get_time,
    "get_date":               _tool_get_date,
    "time_date":              _tool_time_date,
    "get_datetime":           _tool_get_datetime,
    "get_weather":            _tool_get_weather,
    "open_app":               _tool_open_app,
    "take_screenshot":        _tool_take_screenshot,
    "read_screen":            _tool_read_screen,
    "ask_about_screen":       _tool_read_screen,
    "screen_vision":          _tool_read_screen,
    "type_text":              _tool_type_text,
    "search_and_type":        _tool_search_and_type,
    "press_key":              _tool_press_key,
    "click_screen":           _tool_click_screen,
    "screen_act":             _tool_screen_act,
    "act_in_app":             _tool_screen_act,
    "read_window":            _tool_read_window,
    "new_tab":                _tool_new_tab,
    "close_app":              _tool_close_app,
    "window_management":      _tool_window_management,
    "window_mgmt":            _tool_window_management,
    "calculate":              _tool_calculate,
    "set_volume":             _tool_set_volume,
    "set_brightness":         _tool_set_brightness,
    "open_settings":          _tool_open_settings,
    "change_setting":         _tool_change_setting,
    "get_setting":            _tool_get_setting,
    "toggle_setting":         _tool_change_setting,
    "control_windows_system": _tool_control_windows_system,
    "screen_pen_annotate":    _tool_screen_pen_annotate,
    "screen_pen_guide":       _tool_screen_pen_guide,
    "screen_pen":             _tool_screen_pen_annotate,
    "screen_annotate":        _tool_screen_pen_annotate,
    "open_history":           _tool_open_history,
    "system_status":          _tool_system_status,
    "hardware_metrics":       _tool_system_status,
    "system_control":         _tool_system_control,
    "power_control":          _tool_system_control,
    "restart_pc":             _tool_restart_pc,
    "media_control":          _tool_media_control,
    "pause_media":            _tool_pause_media,
    "play_media":             _tool_play_media,
    "resume_media":           _tool_play_media,
    "resume_playback":        _tool_play_media,
    "stock_quote":            _tool_stock_quote,
    "get_stock_quote":        _tool_stock_quote,
    "market_quote":           _tool_stock_quote,
    "stock_price":            _tool_stock_quote,
    "stop":                   _tool_stop,
    "stop_speaking":          _tool_stop_speaking,
    "next_track":             _tool_next_track,
    "prev_track":             _tool_prev_track,
    "set_timer":              _tool_set_timer,
    "stopwatch":              _tool_set_timer,
    "set_reminder":           _tool_set_reminder,
    "list_reminders":         _tool_list_reminders,
    "cancel_reminder":        _tool_cancel_reminder,
    "snooze_reminder":        _tool_snooze_reminder,
    "start_focus_mode":       _tool_start_focus_mode,
    "focus_mode":             _tool_start_focus_mode,
    "morning_briefing":       _tool_morning_briefing,
    "open_folder":            _tool_open_folder,
    "find_file":              _tool_find_file,
    "find_document":          _tool_find_file,
    "open_file":              _tool_file_action,
    "reveal_file":            _make_file_handler("reveal"),
    "copy_file_path":         _make_file_handler("copy_path"),
    "memory_recall":          _tool_memory_recall,
    "memory_store":           _tool_memory_recall,
    "current_media":          _tool_get_current_media,
    "get_current_media":      _tool_get_current_media,
    "clarification":          _tool_clarification,
    "exit":                   _tool_exit,
    "chat":                   _tool_chat,
    "generate_content":       _tool_generate_content,
    "regenerate_content":     _tool_regenerate_content,
    "revert_content":         _tool_revert_content,
    "insert_content":         _tool_insert_content,
    "empty_recycle_bin":      _tool_empty_recycle_bin,
    "lock_pc":                _tool_lock_pc,
    "sleep_pc":               _tool_sleep_pc,
    "blocked":                _tool_blocked,
    "ask_document":           _tool_ask_document,
    "document_qa":            _tool_ask_document,
    "summarize_document":     _tool_summarize_document,
    "search_knowledge":       _tool_search_knowledge,
    "read_emails":            _tool_read_emails,
    "search_emails":          _tool_search_emails,
    "unread_emails":          _tool_unread_emails,
    "draft_email":            _tool_draft_email,
    "get_calendar":           _tool_get_calendar,
    "search_calendar":        _tool_search_calendar,
    "confirm_action":         _tool_confirm_action,
    "cancel_action":          _tool_cancel_action,
}

for _key, (_func, _msg) in _SIMPLE_OS_ACTIONS.items():
    UI_TOOL_HANDLERS.setdefault(_key, _make_simple_handler(_func, _msg))


# ═══════════════════════════════════════════════════════════════
#  Tool Definitions & Multi-Turn / Multi-Step Intent Router
# ═══════════════════════════════════════════════════════════════

def _p(type_: str, description: str = "") -> dict:
    return {"type": type_, "description": description}


def _tool(
    name: str,
    description: str,
    props: dict | None = None,
    required: list | None = None,
    tool_aliases: dict | None = None,
) -> dict:
    entry = {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []},
    }
    if tool_aliases:
        entry["tool_aliases"] = tool_aliases
    return entry


TOOL_DEFINITIONS = [
    _tool(
        "document_qa",
        "Search, read, extract facts, or answer questions from indexed local documents, PDFs, DOCX, notes, or reports.",
        {
            "query": _p("string", "Specific question or search terms for indexed documents"),
            "filepath": _p("string", "Optional document filename or topic hint"),
            "target_index": _p("integer", "Optional ordinal index from recent results: 0=first, 1=second, -1=last"),
        },
        ["query"],
        tool_aliases={"ask_document": {}},
    ),
    _tool(
        "summarize_document",
        "Summarize the full contents of a specific local document or active file.",
        {
            "filepath": _p("string", "Optional concrete filename or path"),
            "target_index": _p("integer", "Optional ordinal index: 0=first, 1=second, -1=last"),
        },
    ),
    _tool(
        "find_file",
        "Search and list matching local file paths on disk by filename or keyword.",
        {"query": _p("string", "Search keyword or filename")},
        ["query"],
    ),
    _tool(
        "open_file",
        "Open an existing local file in its default desktop viewer.",
        {
            "path": _p("string", "Concrete filename or path"),
            "target_index": _p("integer", "Ordinal index: 0=first, 1=second, -1=last"),
        },
    ),
    _tool(
        "chat",
        "General knowledge, explanations, comparisons, reasoning, conversation, or clarifying questions.",
        {"response": _p("string", "Direct spoken reply")},
    ),
    _tool(
        "web_search",
        "Search the web for current news, live information, or online research.",
        {"query": _p("string", "Search query")},
        ["query"],
    ),
    _tool(
        "show_images",
        "Search and display images or visual diagrams for a topic on the overlay.",
        {"query": _p("string", "Subject or concept to display images for")},
        ["query"],
    ),
    _tool(
        "stock_quote",
        "Fetch live price quote for a financial ticker or market asset.",
        {"symbol": _p("string", "Ticker or asset symbol")},
        ["symbol"],
    ),
    _tool(
        "time_date",
        "Report current local clock time, calendar date, or both.",
        {"action": _p("string", "time | date | both")},
        ["action"],
    ),
    _tool(
        "media_control",
        "Control or inspect media playback (play, pause, next, previous, status).",
        {"action": _p("string", "play | pause | next | previous | status")},
        ["action"],
    ),
    _tool(
        "play_youtube",
        "Search YouTube by title, artist, or query to start playback.",
        {"query": _p("string", "Media title or search query")},
        ["query"],
    ),
    _tool(
        "set_volume",
        "Adjust system audio volume (mute, unmute, volume_up, volume_down, or set_volume with level 0-100).",
        {
            "action": _p("string", "mute | unmute | volume_up | volume_down | set_volume"),
            "level": _p("integer", "Numeric 0-100 when action='set_volume'"),
        },
        tool_aliases={"volume_up": {"action": "volume_up"}, "volume_down": {"action": "volume_down"}, "mute": {"action": "mute"}},
    ),
    _tool(
        "set_brightness",
        "Adjust display brightness percentage (0-100).",
        {"level": _p("integer", "Brightness percentage 0-100")},
        ["level"],
    ),
    _tool("take_screenshot", "Capture and save a screenshot of the display.", {}),
    _tool("read_screen", "Inspect visible screen text, active window, or UI contents.", {"question": _p("string", "Question about visible screen contents")}),
    _tool(
        "act_in_app",
        "Perform UI actions inside desktop windows (click, fill, type, press, scroll, choose, or ordered steps).",
        {
            "app": _p("string", "App name or 'front'"),
            "steps": _p("array", "Ordered step objects"),
            "action": _p("string", "click | fill | type | press | scroll | choose"),
            "target": _p("string", "Control text, field label, or direction"),
            "text": _p("string", "Text to type/fill or key to press"),
        },
        tool_aliases={"screen_act": {}},
    ),
    _tool("read_window", "Inspect controls and visible text of the front or named window.", {"target": _p("string", "'front' or app name")}),
    _tool("open_website", "Open a URL or domain in the browser.", {"url": _p("string", "URL or domain"), "title": _p("string", "Optional title")}, ["url"]),
    _tool("open_app", "Launch an installed Windows application by name.", {"name": _p("string", "Application name")}, ["name"]),
    _tool("close_app", "Close a running desktop application or window.", {"name": _p("string", "Application name")}),
    _tool("window_mgmt", "Manage window state (minimize_all, maximize, restore, switch_window).", {"action": _p("string", "Window action")}, ["action"]),
    _tool("power_control", "Control PC power state (lock, sleep, restart, shutdown, cancel_shutdown).", {"action": _p("string", "Power action")}, ["action"]),
    _tool("empty_recycle_bin", "Empty the Windows Recycle Bin.", {}),
    _tool("system_status", "Report PC hardware utilization and status.", {}),
    _tool("open_settings", "Open Windows Settings or a named section.", {"setting": _p("string", "Section name or 'home'")}),
    _tool("control_windows_system", "Control hardware power profiles or modes.", {"action": _p("string", "Profile or mode name")}),
    _tool(
        "screen_pen_annotate",
        "Render diagrams, charts, maps, models, or on-screen UI marks when explicitly requested.",
        {
            "action": _p("string", "diagram | flow | graph | bars | table | timeline | math | geometry | molecule | map | quiz | model | card | circle | ring | arrow | box | underline | highlight | spotlight | clear"),
            "topic": _p("string", "Topic or subject to visualize"),
            "data": _p("object", "Optional structured payload"),
            "label": _p("string", "On-screen UI element text to mark"),
            "x": _p("number", "Normalized X coordinate (0-1000)"),
            "y": _p("number", "Normalized Y coordinate (0-1000)"),
            "color": _p("string", "Palette tone"),
        },
        tool_aliases={"screen_pen": {}, "screen_annotate": {}},
    ),
    _tool(
        "screen_pen_guide",
        "Draw an interactive step-by-step on-screen walkthrough.",
        {"steps": _p("array", "Ordered steps"), "topic": _p("string", "Walkthrough title"), "app": _p("string", "Target app")},
    ),
    _tool("change_setting", "Change or toggle a named system setting.", {"setting": _p("string", "Setting name"), "value": _p("string", "on | off | toggle")}, ["setting"]),
    _tool("get_setting", "Check the state of a named system setting.", {"setting": _p("string", "Setting name")}, ["setting"]),
    _tool("open_history", "Open or close the history drawer.", {"action": _p("string", "open | close")}),
    _tool("get_weather", "Fetch current weather or forecast.", {"city": _p("string", "City name")}),
    _tool("set_timer", "Start a countdown timer.", {"duration": _p("string", "Duration"), "seconds": _p("integer", "Seconds"), "label": _p("string", "Label")}),
    _tool("set_reminder", "Schedule a reminder for a future time.", {"message": _p("string", "Reminder message"), "time": _p("string", "Time or offset")}, ["message", "time"]),
    _tool("list_reminders", "List active scheduled reminders.", {}),
    _tool("cancel_reminder", "Cancel a scheduled reminder or timer.", {"query": _p("string", "Reminder topic"), "all": _p("boolean", "Cancel all")}),
    _tool("snooze_reminder", "Snooze a reminder for a duration.", {"duration": _p("string", "Duration"), "duration_seconds": _p("number", "Seconds"), "reminder_id": _p("string", "ID")}),
    _tool("start_focus_mode", "Start a focus session.", {"duration_minutes": _p("integer", "Minutes"), "goal": _p("string", "Goal")}),
    _tool("morning_briefing", "Provide a daily overview of weather, schedule, reminders, and system status.", {}),
    _tool("memory_recall", "Store or recall user facts and preferences.", {"action": _p("string", "store | recall"), "fact": _p("string", "Fact"), "query": _p("string", "Query")}, ["action"]),
    _tool("get_calendar", "Retrieve upcoming calendar events.", {"days": _p("integer", "Days ahead")}),
    _tool("read_emails", "Read, summarize, or search emails.", {"action": _p("string", "read | search | unread"), "query": _p("string", "Query"), "count": _p("integer", "Max emails")}),
    _tool("unread_emails", "Report unread email count and subjects.", {}),
    _tool("draft_email", "Compose an email draft (supports '$prev').", {"to": _p("string", "Recipient"), "subject": _p("string", "Subject"), "prompt": _p("string", "Prompt")}, ["prompt"]),
    _tool("generate_content", "Draft structured content such as code, letters, essays, or summaries.", {"type": _p("string", "Content type"), "topic": _p("string", "Topic"), "tone": _p("string", "Tone"), "destination": _p("string", "Target app")}, ["type", "topic"]),
    _tool("regenerate_content", "Revise the last generated draft.", {"feedback": _p("string", "Revision instructions")}, ["feedback"]),
    _tool("revert_content", "Revert active draft to previous version.", {}),
    _tool("insert_content", "Insert active draft into a target app.", {"destination": _p("string", "Target app")}, ["destination"]),
    _tool("type_text", "Type text into active or target window (supports '$prev').", {"text": _p("string", "Text"), "app": _p("string", "Target app")}, ["text"]),
    _tool("press_key", "Send a key or shortcut.", {"keys": _p("string", "Key or shortcut")}, ["keys"]),
    _tool("confirm_action", "Confirm a pending action.", {}),
    _tool("cancel_action", "Cancel a pending action.", {}),
    _tool("get_clipboard", "Read text from the clipboard.", {}),
    _tool("stop", "Stop active speech output.", {}),
    _tool("exit", "Exit the assistant.", {}),
    _tool("manage_voices", "List or switch TTS voices.", {"action": _p("string", "list | set"), "voice": _p("string", "Voice name or ID"), "gender": _p("string", "Gender")}, ["action"]),
]

VALID_TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS} | set(UI_TOOL_HANDLERS.keys())
_TOOL_ALIAS_MAP = {
    alias: (td["name"], defaults)
    for td in TOOL_DEFINITIONS
    for alias, defaults in (td.get("tool_aliases") or {}).items()
}
_TOOLS_BLOCK_CACHE: str | None = None
_MASTER_PROMPT_CACHE: str | None = None
_TOOL_SCHEMA_MAP: dict[str, dict] = {}


def register_tool(
    name: str,
    handler: Any,
    description: str = "",
    parameters: dict | None = None,
    required: list | None = None,
) -> None:
    global _TOOLS_BLOCK_CACHE, _MASTER_PROMPT_CACHE, _TOOL_SCHEMA_MAP
    entry = _tool(name, description, parameters or {}, required or [])
    with _state_lock:
        for idx, item in enumerate(TOOL_DEFINITIONS):
            if item.get("name") == name:
                TOOL_DEFINITIONS[idx] = entry
                break
        else:
            TOOL_DEFINITIONS.append(entry)
        UI_TOOL_HANDLERS[name] = handler
        VALID_TOOL_NAMES.add(name)
        _TOOLS_BLOCK_CACHE = None
        _MASTER_PROMPT_CACHE = None
        _TOOL_SCHEMA_MAP = {}


def _tools_block() -> str:
    global _TOOLS_BLOCK_CACHE
    with _state_lock:
        if _TOOLS_BLOCK_CACHE is None:
            rows = []
            for t in TOOL_DEFINITIONS:
                props = (t.get("parameters") or {}).get("properties") or {}
                req = set((t.get("parameters") or {}).get("required") or [])
                sig = ", ".join(f"{k}*" if k in req else k for k in props)
                rows.append(f"- {t['name']}({sig}): {t['description']}")
            _TOOLS_BLOCK_CACHE = "\n".join(rows)
        return _TOOLS_BLOCK_CACHE


_MASTER_SYSTEM_PROMPT = """You are Amigo, a fast voice and desktop AI assistant on Windows.

CORE PRINCIPLES:
1. Map the user's intent to the best single tool or ordered sequence of tools in TOOLS AVAILABLE.
2. Chain multi-step requests using "$prev" for prior step output when needed.
3. Ground parameters strictly in the user's request, context, or conversation history; never fabricate targets.
4. Use `document_qa` for questions about local/indexed files; use `summarize_document` for full-document summaries; use `chat` for explanations, comparisons, and general Q&A (never chain `chat` with `screen_pen_annotate`, and never expose internal tool names or JSON/schema terms in spoken replies).
5. Output ONLY valid JSON: {"actions": [{"tool": "<tool_name>", "params": {...}}]}

TOOLS AVAILABLE:
<<TOOLS>>
"""


def get_static_master_prompt() -> str:
    global _MASTER_PROMPT_CACHE
    with _state_lock:
        if _MASTER_PROMPT_CACHE is None:
            _MASTER_PROMPT_CACHE = _MASTER_SYSTEM_PROMPT.replace("<<TOOLS>>", _tools_block())
        return _MASTER_PROMPT_CACHE


def build_agent_system_prompt(query: str = "", conversation_history: list | None = None) -> str:
    prefix = get_static_master_prompt()
    state_lines = []
    if pending := _pending_confirmation_line():
        state_lines.append(pending)
    state = get_active_state(clean_expired=True)
    if gen_info := state.get("generated_content"):
        if isinstance(gen_info, dict) and gen_info.get("content"):
            state_lines.append(f"Active Generated Draft: {gen_info.get('type', 'content')} about '{gen_info.get('topic', '')}'")
    if active_ctx := rag_engine.get_active_context_prompt():
        state_lines.append(active_ctx)
    return f"{prefix}\n\nACTIVE STATE:\n" + "\n".join(state_lines) if state_lines else prefix


def _build_router_messages(query: str, conversation_history: list | None = None) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": get_static_master_prompt()}]
    messages.extend(build_unified_history_messages(conversation_history=conversation_history, max_turns=3, for_router=True))
    pending = _pending_confirmation_line()
    runtime_ctx = build_turn_runtime_context(query, pending_confirmation=pending)
    user_payload = (
        f"{runtime_ctx}\n\nUser Question: {query}\n[Mode: Voice | Output: JSON]"
        if runtime_ctx
        else f"{query}\n[Mode: Voice | Output: JSON]"
    )
    messages.append({"role": "user", "content": user_payload})
    return messages


_JSON_DECODER = json.JSONDecoder()
_ROUTER_STOP = ["<|endoftext|>", "<｜end of sentence｜>", "<|im_end|>", "<|im_start|>", "<eos>", "<turn|>", "<|turn|>"]


def _clean_json_text(text: str) -> str:
    from amigo.core.llm_agent import _strip_reasoning
    clean, _ = _strip_reasoning(text)
    clean = re.sub(r"```(?:json)?\s*", "", clean).replace("```", "").strip()
    clean = re.sub(r",\s*([\]}])", r"\1", clean)
    return clean


def _iter_json_objects(text: str):
    pos = 0
    while True:
        start = text.find("{", pos)
        if start == -1:
            return
        try:
            obj, end = _JSON_DECODER.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        yield start, end, obj
        pos = end


def _coerce_params(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return {k: v for k, v in raw.items() if k != "confirmed"} if isinstance(raw, dict) else {}


def _call_from_obj(obj: Any) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    if "actions" in obj:
        try:
            validated = ActionPlanModel.model_validate(obj)
            calls: list[dict] = []
            for item in validated.actions:
                calls.extend(_call_from_obj(item.model_dump()))
            return calls
        except ValidationError:
            pass
    tool_cand = obj.get("tool") or obj.get("action") or obj.get("name")
    if not isinstance(tool_cand, str):
        return []
    t_name = re.sub(r"\([^)]*\)$", "", tool_cand.strip()).strip().lower()
    if t_name not in VALID_TOOL_NAMES:
        return []
    params = _coerce_params(obj.get("params") or obj.get("arguments") or obj.get("parameters") or {})
    speak = obj.get("speak") or (params.get("reply") or params.get("response") if t_name == "chat" else "")
    if t_name == "chat":
        reply_val = params.get("reply") or params.get("response") or speak
        if reply_val:
            params["response"] = reply_val
            params["reply"] = reply_val
            speak = reply_val
    return [{"tool": t_name, "params": params, "speak": speak if isinstance(speak, str) else ""}]


def _parse_tool_calls(response: str) -> list[dict]:
    calls: list[dict] = []

    for m in re.finditer(r'<invoke\s+name=["\']([^"\']+)["\']\s*>([\s\S]*?)</invoke>', response, flags=re.I):
        fn_name = m.group(1).strip().lower()
        params = {
            p.group(1).strip(): p.group(2).strip()
            for p in re.finditer(r'<parameter\s+name=["\']([^"\']+)["\']\s*>([\s\S]*?)</parameter>', m.group(2), flags=re.I)
        }
        if fn_name in VALID_TOOL_NAMES:
            calls.append({"tool": fn_name, "params": params, "speak": ""})

    xml_blocks = re.findall(
        r'<(?:tool_call|invoke|function_call)(?:[^>]*)>([\s\S]*?)</(?:tool_call|invoke|function_call)>',
        response,
        flags=re.I,
    )
    for block in xml_blocks:
        block_str = block.strip()
        if "{" in block_str:
            for _, _, obj in _iter_json_objects(block_str):
                calls.extend(_call_from_obj(obj))
        fn_match = re.search(r'<function=([a-zA-Z0-9_]+)(?:\((.*?)\)+)?>?([\s\S]*?)</function>', block_str, flags=re.I)
        if fn_match:
            fn_name = fn_match.group(1).strip().lower()
            inline_args = fn_match.group(2) or ""
            body = fn_match.group(3) or ""
            params = {
                k.strip(): v.strip()
                for k, v in re.findall(r'([a-zA-Z0-9_]+)\s*=\s*["\']([^"\']*)["\']', inline_args)
            }
            for p in re.finditer(r'<parameter=([a-zA-Z0-9_]+)>\s*([\s\S]*?)\s*</parameter>', body, flags=re.I):
                params[p.group(1).strip()] = p.group(2).strip()
            if fn_name in VALID_TOOL_NAMES:
                calls.append({"tool": fn_name, "params": params, "speak": ""})

    clean = _clean_json_text(response)
    for _, _, obj in _iter_json_objects(clean):
        calls.extend(_call_from_obj(obj))

    deduped: list[dict] = []
    for c in calls:
        if c["tool"] in VALID_TOOL_NAMES and (
            not deduped or not (c["tool"] == deduped[-1]["tool"] and c.get("params") == deduped[-1].get("params"))
        ):
            deduped.append(c)
    return deduped


def _clean_final_dialogue(text: str) -> str:
    if not text:
        return ""
    from amigo.core.llm_agent import _strip_reasoning
    clean, _ = _strip_reasoning(text)
    return clean


def _extract_final_response(response: str, tool_calls: list) -> str:
    clean = _clean_json_text(response)
    for _, _, obj in _iter_json_objects(clean):
        if isinstance(obj, dict):
            for key in ("response", "speak", "reply"):
                if isinstance(obj.get(key), str) and obj[key].strip():
                    return _clean_final_dialogue(obj[key])
            if isinstance(obj.get("actions"), list):
                for act in obj["actions"]:
                    if isinstance(act, dict):
                        p = act.get("params") or {}
                        for key in ("response", "reply", "speak"):
                            if isinstance(p.get(key), str) and p[key].strip():
                                return _clean_final_dialogue(p[key])

    non_tool = re.sub(
        r'<(?:tool_call|invoke|function_call)(?:[^>]*)>[\s\S]*?</(?:tool_call|invoke|function_call)>',
        "",
        clean,
        flags=re.I,
    ).strip()
    non_tool = re.sub(r"```(?:json|xml)?\s*", "", non_tool).replace("```", "").strip()
    from amigo.core.llm_agent import _strip_reasoning
    non_tool, _ = _strip_reasoning(non_tool)

    if not tool_calls and non_tool and not non_tool.startswith(("{", "[")):
        return _clean_final_dialogue(non_tool)
    return ""


def _chat_action(text: str = "") -> dict:
    return {"tool": "chat", "params": {"response": text} if text else {}, "speak": text}


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    if not query or not query.strip():
        return {"tool": "chat", "params": {"response": ""}, "speak": ""}
    return None


def get_tool_schema(tool_name: str) -> dict | None:
    global _TOOL_SCHEMA_MAP
    with _state_lock:
        if not _TOOL_SCHEMA_MAP:
            _TOOL_SCHEMA_MAP = {t["name"]: t for t in TOOL_DEFINITIONS}
        if tool_name in _TOOL_SCHEMA_MAP:
            return _TOOL_SCHEMA_MAP[tool_name]
        if tool_name in _TOOL_ALIAS_MAP:
            target, _ = _TOOL_ALIAS_MAP[tool_name]
            return _TOOL_SCHEMA_MAP.get(target)
        return None


_MEDIA_ACTION_MAP = {
    "resume": "resume_media",
    "play": "resume_media",
    "pause": "pause_media",
    "next": "next_track",
    "skip": "next_track",
    "prev": "prev_track",
    "previous": "prev_track",
    "status": "current_media",
    "current": "current_media",
}

_DATETIME_KIND_MAP = {
    "time": "get_time",
    "date": "get_date",
    "both": "time_date",
}

_POWER_ACTION_MAP = {
    "lock": "lock_pc",
    "lock_pc": "lock_pc",
    "sleep": "sleep_pc",
    "sleep_pc": "sleep_pc",
    "restart": "restart_pc",
    "restart_pc": "restart_pc",
    "reboot": "restart_pc",
    "shutdown": "system_control",
    "shutdown_pc": "system_control",
    "cancel_shutdown": "system_control",
}


def _build_grounding_text(query: str, conversation_history: list | None = None) -> str:
    parts = [_as_str(query)]
    if conversation_history:
        for turn in conversation_history[-3:]:
            if isinstance(turn, dict):
                parts.extend(
                    _as_str(turn.get(k))
                    for k in ("user", "assistant", "assistant_reply", "response", "text", "content")
                    if turn.get(k)
                )
            elif turn:
                parts.append(str(turn))
    return " ".join(p for p in parts if p)


def _is_tokens_grounded(value: str, grounding_text: str) -> bool:
    if not value or not grounding_text:
        return False
    g_low = grounding_text.lower()
    g_squash = re.sub(r"[^\w]+", "", g_low, flags=re.UNICODE)
    v_squash = re.sub(r"[^\w]+", "", value.lower(), flags=re.UNICODE)
    if len(v_squash) >= 3 and (v_squash in g_squash or (len(g_squash) >= 4 and g_squash in v_squash)):
        return True
    g_toks = re.findall(r"\w+", g_low, flags=re.UNICODE)
    v_toks = [t for t in re.findall(r"\w+", value.lower(), flags=re.UNICODE) if len(t) >= 2]
    if not v_toks or not g_toks:
        return False
    for vt in v_toks:
        for gt in g_toks:
            if len(gt) < 2:
                continue
            if vt == gt or (len(gt) >= 4 and (gt in vt or vt in gt)):
                return True
            if (
                len(vt) >= 4
                and len(gt) >= 4
                and difflib.SequenceMatcher(None, vt, gt).ratio() >= _GROUNDING_SIMILARITY_THRESHOLD
            ):
                return True
    return False


def _is_valid_percentage(lvl: Any) -> bool:
    try:
        return lvl is not None and not isinstance(lvl, bool) and 0 <= int(lvl) <= 100
    except (ValueError, TypeError):
        return False


def _validate_tool_specific_params(t_name: str, call_params: dict, has_prior_output: bool) -> list[str]:
    errors: list[str] = []
    if t_name == "set_volume":
        act = _as_str(call_params.get("action")).lower()
        if act in ("set_volume", "set") or (not act and call_params.get("level") is not None):
            if not _is_valid_percentage(call_params.get("level")):
                errors.append("Tool 'set_volume' with action='set_volume' requires a numeric 'level' (0-100).")
    elif t_name == "set_brightness":
        if not _is_valid_percentage(call_params.get("level")):
            errors.append("Tool 'set_brightness' requires a numeric 'level' (0-100).")
    elif t_name == "memory_recall":
        act = _as_str(call_params.get("action")).lower()
        if act == "store" and not (call_params.get("fact") or call_params.get("content") or call_params.get("query") or has_prior_output):
            errors.append("Tool 'memory_recall' with action='store' requires 'fact'.")
        elif act == "recall" and not (call_params.get("query") or call_params.get("fact")):
            errors.append("Tool 'memory_recall' with action='recall' requires 'query'.")
    elif t_name == "open_website":
        site_url = _as_str(call_params.get("url"))
        parsed_url = urllib.parse.urlparse(site_url if "://" in site_url else f"https://{site_url}")
        if not site_url or "." not in (parsed_url.netloc or ""):
            errors.append("Tool 'open_website' requires a valid domain or URL in 'url'.")
    elif t_name == "read_emails":
        if _as_str(call_params.get("action")).lower() == "search" and not _as_str(call_params.get("query")):
            errors.append("Tool 'read_emails' with action='search' requires 'query'.")
    elif t_name == "media_control":
        if _as_str(call_params.get("action")).lower() not in _MEDIA_ACTION_MAP:
            errors.append("Tool 'media_control' requires 'action' in: resume, pause, next, prev, status.")
    elif t_name in ("time_date", "get_datetime"):
        kind = _as_str(call_params.get("action") or call_params.get("kind")).lower()
        if kind not in _DATETIME_KIND_MAP:
            errors.append(f"Tool '{t_name}' requires 'action' in: time, date, both.")
    elif t_name == "power_control":
        if _as_str(call_params.get("action")).lower() not in _POWER_ACTION_MAP:
            errors.append("Tool 'power_control' requires 'action' in: lock, sleep, restart, shutdown, cancel_shutdown.")
    elif t_name in ("screen_act", "act_in_app"):
        raw_steps = call_params.get("steps")
        has_steps = (isinstance(raw_steps, list) and bool(raw_steps)) or (
            isinstance(raw_steps, str) and raw_steps.strip().startswith("[")
        )
        if not has_steps:
            act = _as_str(call_params.get("action") or call_params.get("do")).lower()
            tgt = _as_str(call_params.get("target") or call_params.get("field"))
            txt = _as_str(call_params.get("text") or call_params.get("option"))
            if act not in _SCREEN_ACT_ACTIONS and act not in ("click_text", "type_text", "press_key", "choose", "wait", "click_at"):
                errors.append(f"Tool '{t_name}' requires a valid 'action' or 'steps' array.")
            elif act in ("click", "click_text") and not tgt:
                errors.append(f"Tool '{t_name}' with action='click' requires 'target'.")
            elif act == "fill" and (not tgt or not txt):
                errors.append(f"Tool '{t_name}' with action='fill' requires 'target' and 'text'.")
            elif act in ("type", "type_text") and not (txt or has_prior_output):
                errors.append(f"Tool '{t_name}' with action='type' requires 'text'.")
            elif act in ("press", "key", "press_key") and not (txt or tgt):
                errors.append(f"Tool '{t_name}' with action='press' requires 'text' or 'target'.")
    return errors


def _validate_tool_grounding(t_name: str, call_params: dict, grounding_text: str) -> list[str]:
    errors: list[str] = []
    if t_name == "open_app":
        app_val = _as_str(call_params.get("name"))
        if app_val and not _is_tokens_grounded(app_val, grounding_text):
            errors.append(f"Tool 'open_app' parameter name='{app_val}' is not grounded.")
    elif t_name in ("change_setting", "get_setting"):
        set_val = _as_str(call_params.get("setting"))
        if set_val and not _is_tokens_grounded(set_val, grounding_text):
            errors.append(f"Tool '{t_name}' parameter setting='{set_val}' is not grounded.")
    elif t_name == "control_windows_system":
        cat_val = _as_str(call_params.get("category") or call_params.get("action") or call_params.get("mode"))
        if not cat_val or not _is_tokens_grounded(cat_val, grounding_text):
            errors.append(f"Tool 'control_windows_system' target '{cat_val}' is not grounded.")
    elif t_name == "open_website":
        site_url = _as_str(call_params.get("url"))
        parsed_url = urllib.parse.urlparse(site_url if "://" in site_url else f"https://{site_url}")
        host_parts = [p for p in (parsed_url.netloc or "").lower().split(".") if p and p != "www"]
        host_stem = " ".join(host_parts[:-1]) if len(host_parts) > 1 else " ".join(host_parts)
        if not host_stem or not _is_tokens_grounded(host_stem, grounding_text):
            errors.append(f"Tool 'open_website' target '{site_url}' is not grounded.")
    elif t_name in ("set_volume", "set_brightness"):
        act_v = _as_str(call_params.get("action")).lower()
        lvl = call_params.get("level")
        need_lvl = t_name == "set_brightness" or not act_v or act_v in ("set_volume", "set")
        if need_lvl and lvl is not None and not isinstance(lvl, bool):
            try:
                lvl_int = int(lvl)
                if not re.search(rf"\b{lvl_int}\b", grounding_text):
                    errors.append(f"Tool '{t_name}' level='{lvl_int}' is not grounded.")
            except (ValueError, TypeError):
                pass
    elif t_name in ("screen_act", "act_in_app") and not call_params.get("steps"):
        act_s = _as_str(call_params.get("action")).lower()
        tgt_s = _as_str(call_params.get("target") or call_params.get("field"))
        if tgt_s and not (act_s == "scroll" and tgt_s.lower() in _SCROLL_DIRS):
            if not _is_tokens_grounded(tgt_s, grounding_text):
                errors.append(f"Tool '{t_name}' target='{tgt_s}' is not grounded.")
    return errors


def _validate_action_plan(tool_calls: list[dict], grounding_text: str | None = None) -> list[str]:
    errors: list[str] = []
    prior_tools: set[str] = set()
    upstream_output_tools = {
        "web_search", "generate_content", "read_screen", "read_window",
        "get_clipboard", "document_qa", "summarize_document", "find_file",
    }

    for idx, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue
        t_name = call.get("tool")
        if not t_name:
            continue
        schema = get_tool_schema(t_name)
        if not schema:
            prior_tools.add(t_name)
            continue

        params_schema = schema.get("parameters") or {}
        required = list(params_schema.get("required") or [])
        if t_name == "open_app" and "name" not in required:
            required.append("name")
        call_params = call.get("params") or {}

        if t_name == "open_file":
            has_path = bool(_as_str(call_params.get("path")))
            has_idx = call_params.get("target_index") is not None
            if not has_path and not has_idx and "find_file" not in prior_tools:
                errors.append("Tool 'open_file' requires either 'path' or 'target_index'.")
            prior_tools.add(t_name)
            continue

        has_prior_output = idx > 0 and bool(prior_tools & upstream_output_tools)
        missing = [
            req for req in required
            if (call_params.get(req) is None or _as_str(call_params.get(req)) == "")
            and not (has_prior_output and req in ("prompt", "text", "query", "content"))
        ]
        if missing:
            errors.append(f"Tool '{t_name}' is missing required parameter(s): {', '.join(missing)}.")

        errors.extend(_validate_tool_specific_params(t_name, call_params, has_prior_output))
        if grounding_text:
            errors.extend(_validate_tool_grounding(t_name, call_params, grounding_text))

        prior_tools.add(t_name)

    return errors


def _normalize_tool_calls(tool_calls: list[dict], final_response: str) -> None:
    for call in tool_calls:
        if call["tool"] in _TOOL_ALIAS_MAP:
            target_tool, defaults = _TOOL_ALIAS_MAP[call["tool"]]
            call["tool"] = target_tool
            p = call.setdefault("params", {})
            for dk, dv in defaults.items():
                p.setdefault(dk, dv)

        if call["tool"] in ("power_control", "system_control"):
            call.setdefault("params", {}).pop("confirmed", None)

        if call["tool"] == "get_datetime":
            call["tool"] = "time_date"

        if call["tool"] == "set_volume":
            p = call.setdefault("params", {})
            act_v = _as_str(p.get("action")).lower()
            if act_v == "set":
                p["action"] = "set_volume"
            if "level" in p and p.get("level") is not None:
                try:
                    p["level"] = int(p["level"])
                    p["action"] = "set_volume"
                except (ValueError, TypeError):
                    pass
            elif not act_v:
                p["action"] = "set_volume"

        if call["tool"] == "read_emails":
            act = _as_str((call.get("params") or {}).get("action")).lower()
            if act in ("unread", "unread_emails"):
                call["tool"] = "unread_emails"

        if call["tool"] == "chat":
            reply_val = (
                call["params"].get("reply")
                or call["params"].get("response")
                or call["params"].get("question")
                or call["params"].get("message")
                or call.get("speak")
                or final_response
            )
            if reply_val:
                call["params"]["response"] = reply_val
                call["params"]["reply"] = reply_val
                if not call.get("speak"):
                    call["speak"] = reply_val

    if any(c.get("tool") == "chat" for c in tool_calls) and len(tool_calls) > 1:
        tool_calls[:] = [c for c in tool_calls if c.get("tool") != "screen_pen_annotate"]


def _build_clarification_action(query: str, tool_calls: list[dict]) -> tuple[list[dict], str]:
    first_bad_tool = tool_calls[0].get("tool", "action") if tool_calls else "action"
    friendly_tool = first_bad_tool.replace("_", " ")
    clarify_msg = f"Could you clarify the exact {friendly_tool} details you'd like me to use?"
    return [{
        "tool": "clarification",
        "params": {"query": query, "message": clarify_msg, "candidate_tools": [first_bad_tool]},
        "speak": clarify_msg,
    }], clarify_msg


def _route_with_llm(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    messages = _build_router_messages(query, conversation_history=conversation_history)
    grounding_text = _build_grounding_text(query, conversation_history=conversation_history)
    try:
        response = query_local_llm(
            messages,
            system_prompt="",
            max_tokens=_ROUTE_MAX_TOKENS,
            temperature=0.0,
            repeat_penalty=1.0,
            thinking=False,
            sanitize=False,
            stop=_ROUTER_STOP,
            raise_on_error=True,
        )
    except Exception as e:
        logger.error("[Tool Registry] Routing failed: %s", e)
        msg = "I ran into a problem understanding that. Could you try again?"
        return [_chat_action(msg)], msg

    tool_calls = _parse_tool_calls(response)
    schema_errors = _validate_action_plan(tool_calls, grounding_text=grounding_text)

    if schema_errors and tool_calls:
        repair_msg = (
            "Your previous action plan had validation errors:\n"
            + "\n".join(f"- {e}" for e in schema_errors)
            + f"\nOriginal user query: '{query}'\n"
            "Output a corrected JSON action plan using ONLY values stated by the user or in context."
        )
        try:
            repair_response = query_local_llm(
                [*messages, {"role": "assistant", "content": response}, {"role": "user", "content": repair_msg}],
                system_prompt="",
                max_tokens=_REPAIR_MAX_TOKENS,
                temperature=0.0,
                repeat_penalty=1.0,
                thinking=False,
                sanitize=False,
                stop=_ROUTER_STOP,
                raise_on_error=False,
            )
            repaired_calls = _parse_tool_calls(repair_response)
            repaired_errors = _validate_action_plan(repaired_calls, grounding_text=grounding_text)
            if repaired_calls and not repaired_errors:
                tool_calls = repaired_calls
                response = repair_response
                schema_errors = []
            else:
                schema_errors = repaired_errors
        except Exception as repair_err:
            logger.warning("[Tool Registry] Repair re-prompt error: %s", repair_err)

    if schema_errors:
        logger.warning("[Tool Registry] Action plan invalid: %s", schema_errors)
        return _build_clarification_action(query, tool_calls)

    final_response = _extract_final_response(response, tool_calls)
    _normalize_tool_calls(tool_calls, final_response)

    if tool_calls:
        return tool_calls, final_response
    return [_chat_action(final_response)], final_response


def get_agent_actions(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    if not query or not query.strip():
        return [{"tool": "chat", "params": {}, "speak": ""}], ""
    cleaned = query.strip()
    fast = parse_user_intent_fast(cleaned)
    if fast is not None:
        return [fast], fast.get("speak", "")
    return _route_with_llm(cleaned, conversation_history)


def get_agent_action(query: str, conversation_history: list | None = None) -> list[dict]:
    return get_agent_actions(query, conversation_history)[0]


def get_last_played_song(conversation_history: list | None = None) -> dict | None:
    if conversation_history:
        for turn in reversed(conversation_history):
            if isinstance(turn, dict) and turn.get("tool") == "play_youtube":
                q = _as_str((turn.get("params") or {}).get("query") or turn.get("user"))
                if q:
                    return {"title": q, "query": q, "url": ""}
        return None

    media = get_active_state(clean_expired=True).get("current_media")
    if isinstance(media, dict) and media.get("status") == "playing":
        title = media.get("title") or media.get("query")
        if title:
            return {"title": title, "query": media.get("query") or title, "url": media.get("url", "")}
    return None


TOOL_ACTION_PROMPTS = {
    "play_youtube": "play audio or video on YouTube",
    "web_search": "search the web",
    "show_images": "show images or photos on screen",
    "chat": "explain or discuss this",
    "open_app": "open an application",
    "close_app": "close an application",
    "window_mgmt": "manage windows",
    "current_media": "check active media",
    "pause_media": "pause media playback",
    "play_media": "resume media playback",
    "next_track": "skip to the next track",
    "prev_track": "go to the previous track",
    "set_volume": "adjust volume",
    "get_time": "check the current time",
    "get_date": "check the current date",
    "get_weather": "check the weather",
    "stock_quote": "check a market price",
    "system_control": "perform a system action",
    "take_screenshot": "take a screenshot",
    "lock_pc": "lock the PC",
    "sleep_pc": "sleep the PC",
    "restart_pc": "restart the PC",
    "system_status": "check system status",
    "empty_recycle_bin": "empty the recycle bin",
    "screen_vision": "inspect the screen",
    "memory_recall": "check saved memory",
    "document_qa": "search indexed documents",
    "get_calendar": "check calendar events",
    "unread_emails": "check unread emails",
    "set_timer": "set a timer",
    "set_reminder": "set a reminder",
    "find_file": "find local files",
    "type_text": "type text",
    "press_key": "press a key or shortcut",
    "click_screen": "click on screen",
}


def format_clarification_prompt(tool_a: str, tool_b: str, query: str) -> str:
    desc_a = TOOL_ACTION_PROMPTS.get(tool_a, f"use {tool_a.replace('_', ' ')}")
    desc_b = TOOL_ACTION_PROMPTS.get(tool_b, f"use {tool_b.replace('_', ' ')}")
    return f"I'm not completely sure — did you want to {desc_a}, or {desc_b}?"


def execute_tool(
    tool: str,
    params: dict | None = None,
    query: str = "",
    spoken: str = "",
    conversation_history: list | None = None,
) -> tuple[str, str | None, dict]:
    with _state_lock:
        handler = UI_TOOL_HANDLERS.get(tool, _tool_chat)
    params = params if isinstance(params, dict) else {}
    _call_ctx.value = {
        "tool": tool,
        "params": params,
        "query": query,
        "spoken": spoken,
        "conversation_history": conversation_history,
    }
    try:
        result = handler(params, query, spoken)
        if isinstance(result, tuple):
            if len(result) >= 3:
                return result[0] or "", result[1], result[2] if isinstance(result[2], dict) else {}
            if len(result) == 2:
                return result[0] or "", result[1], {}
            if len(result) == 1:
                return result[0] or "", None, {}
        return result or "", None, {}
    except Exception as e:
        logger.exception("Error executing tool '%s'", tool)
        return "Sorry, I couldn't complete that.", None, {"error": str(e), "tool": tool}
    finally:
        _call_ctx.value = None


def _inject_prev_output(params: dict, last_tool_output: str) -> dict:
    if not last_tool_output:
        return params
    updated = dict(params)
    replaced_explicit = False
    for k, v in list(updated.items()):
        if isinstance(v, str) and _RE_PREV_PLACEHOLDER.search(v):
            updated[k] = _RE_PREV_PLACEHOLDER.sub(lambda _: last_tool_output, v)
            replaced_explicit = True
    if not replaced_explicit:
        for key in ("content", "text", "context", "prompt", "query", "code", "data"):
            if key in updated and not updated.get(key):
                updated[key] = last_tool_output
                break
        else:
            for key in ("content", "text", "context", "prompt"):
                if not updated.get(key):
                    updated[key] = last_tool_output
                    break
    return updated


def execute_actions(
    actions: list[dict],
    query: str = "",
    conversation_history: list | None = None,
) -> list[tuple[str, str | None, dict]]:
    results = []
    last_tool_output = ""
    valid_actions = [a for a in actions if isinstance(a, dict)]

    for idx, action in enumerate(valid_actions):
        tool = action.get("tool", "chat")
        params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
        params = {k: v for k, v in params.items() if k != "confirmed"}
        spoken = action.get("speak", "") or params.get("speak", "") or params.get("reply", "") or params.get("response", "")
        params = _inject_prev_output(params, last_tool_output)

        res_spoken, res_url, meta = execute_tool(
            tool,
            params,
            query=action.get("step_text") or query,
            spoken=spoken,
            conversation_history=conversation_history,
        )
        results.append((res_spoken, res_url, meta))

        if isinstance(meta, dict):
            if meta.get("requires_confirmation"):
                with _state_lock:
                    if _PENDING_CONFIRMATION is not None and idx + 1 < len(valid_actions):
                        _PENDING_CONFIRMATION["remaining_actions"] = valid_actions[idx + 1 :]
                break
            if meta.get("requires_clarification"):
                break

        out_text = (
            (meta.get("generated_content") or meta.get("screen_text") or meta.get("text_snippet") or meta.get("text") or meta.get("clipboard"))
            if isinstance(meta, dict)
            else ""
        )
        last_tool_output = _as_str(out_text or res_spoken)

    return results