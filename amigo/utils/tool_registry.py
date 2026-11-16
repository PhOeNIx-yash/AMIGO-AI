"""
Tool Registry & Action Dispatcher for Amigo Voice Assistant.
Features:
- Pure LLM-driven single-step and multi-step intent understanding (zero hardcoded keywords/phrase lists).
- 100% static system prompt prefix for instant KV cache reuse in llama.cpp.
- Inter-step output & state chaining ($prev / {{prev}} + automatic active_file/content propagation).
- Multi-step confirmation queue resumption.
- Roamies floating desktop overlay UI emitters (replacing static action cards).
"""

import collections
import datetime
import difflib
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from typing import Any

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
from amigo.services.app_opener import execute_file_action, find_files, is_known_app, is_known_folder, open_folder, open_windows_app
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
    rag_engine.update_active_state(slot, data)


def get_active_state(clean_expired: bool = False) -> dict:
    return rag_engine.get_active_state(clean_expired=clean_expired)


# ── Pre-compiled Structural Patterns ───────────────────────────

_RE_URLS = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]*[^\s<>"{}|\\^`\[\].,;:!?]')
_RE_MEDIA_CLEAN_TITLE = re.compile(r"^(?:play|playing)\s*:\s*", re.IGNORECASE)
_RE_PREV_PLACEHOLDER = re.compile(r"(?:\$prev|\{\{\s*prev\s*\}\})", re.IGNORECASE)

_media_update_cb = None
_ui_broadcast_cb = None
_call_ctx = threading.local()
_PENDING_CONFIRMATION = None
_PENDING_TTL_SECONDS = 30.0


def set_media_update_callback(cb):
    global _media_update_cb
    _media_update_cb = cb


def set_ui_broadcast_callback(cb):
    global _ui_broadcast_cb
    _ui_broadcast_cb = cb


def extract_and_open_urls(text: str) -> bool:
    return bool(_RE_URLS.findall(text))


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

def emit_roamies_card(
    title: str,
    heading: str | None = None,
    text: str | None = None,
    items: list[str] | None = None,
    tone: str | None = None,
    duration: float = 16.0,
    sources: list[dict[str, str]] | None = None,
) -> None:
    """Emit a floating glass card directly to the Roamies desktop transparent overlay."""
    try:
        from amigo.services.screen_pen import _VALID_TONES, push_overlay_event
        push_overlay_event({"type": "ink_clear", "all": True})
        card_tone = tone if (tone and tone in _VALID_TONES) else "teal"
        clean_items = [str(it)[:120] for it in (items or []) if str(it).strip()][:8]
        mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-info-card",
            "title": str(title or "Amigo")[:40],
            "heading": str(heading or title or "")[:36],
            "text": str(text or "")[:400],
            "items": clean_items,
            "tone": card_tone,
        }
        if sources:
            mark["sources"] = sources
        push_overlay_event({
            "type": "ink",
            "id": "amigo-card",
            "seg": 0,
            "last": True,
            "fade": float(duration or 16.0),
            "beats": [{
                "voiced": True,
                "caption": str(heading or title or ""),
                "marks": [mark, {"kind": "sfx", "name": "whoosh"}],
            }],
        })
    except Exception as exc:
        logger.debug("[Roamies Card] Could not push overlay event: %s", exc)


def emit_interactive_stock(quote: Any, duration: float = 22.0) -> None:
    """Emit a stock quote card and 2D trend graph directly onto Roamies overlay."""
    try:
        from amigo.services.screen_pen import push_overlay_event
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

        # Clear any lingering old overlay cards first so they don't stack
        push_overlay_event({"type": "ink_clear", "all": True})

        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-stock-card",
            "title": f"{name} ({sym})"[:40],
            "heading": f"{curr} {price_str}{pct_str}"[:36],
            "text": f"Real-time market quote on {getattr(quote, 'exchange', 'Market') or 'Market'}.",
            "items": stock_items,
            "tone": stock_tone,
        }

        # Build graph points from historical prices or intraday curve
        history = getattr(quote, "history", None)
        if isinstance(history, list) and len(history) >= 2:
            raw_pts = [float(p) for p in history if p is not None]
            step = max(1, len(raw_pts) // 30)
            sampled = raw_pts[::step]
            if sampled and sampled[-1] != raw_pts[-1]:
                sampled.append(raw_pts[-1])
            pts = [[float(i), round(p, 2)] for i, p in enumerate(sampled)]
            min_p = min(sampled)
            max_p = max(sampled)
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
            "plots": [{
                "points": pts,
                "tone": stock_tone,
                "label": f"{sym} Trend",
            }],
            "dots": [{
                "x": float(len(pts) - 1),
                "y": round(price, 2),
                "label": f"${price_str}",
            }],
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

        push_overlay_event({
            "type": "ink",
            "id": "amigo-stock",
            "seg": 0,
            "last": True,
            "fade": float(duration),
            "beats": beats,
        })

        # Avatar emotional response
        try:
            from amigo.services.screen_pen import emit_avatar_act
            emit_avatar_act("hype" if change >= 0 else "motivate")
        except Exception:
            pass
    except Exception as exc:
        logger.debug("[Interactive Stock] Error emitting: %s", exc)


def emit_interactive_setting(setting_name: str, status_desc: str, is_on: bool, duration: float = 18.0) -> None:
    """Emit an interactive setting card with an immediate one-click toggle button."""
    try:
        from amigo.services.screen_pen import push_overlay_event, emit_avatar_act
        s_title = setting_name.title()
        clean_key = setting_name.lower().replace(" ", "").replace("_", "")
        tone = "lime" if is_on else "coral"
        action_verb = "Turn Off" if is_on else "Turn On"

        # Clear old cards first to prevent multiple duplicated cards stacking
        push_overlay_event({"type": "ink_clear", "all": True, "stop": True})

        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-setting-card",
            "title": f"Setting · {s_title}"[:40],
            "heading": f"{s_title}: {'Enabled' if is_on else 'Disabled'}"[:36],
            "text": str(status_desc or "")[:300],
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

        push_overlay_event({
            "type": "ink",
            "id": "amigo-setting",
            "seg": 0,
            "last": True,
            "fade": float(duration),
            "beats": [
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
        })
        emit_avatar_act("hype" if is_on else "wave")
    except Exception as exc:
        logger.debug("[Interactive Setting] Error emitting: %s", exc)


def emit_interactive_volume(level: int | float, duration: float = 16.0) -> None:
    """Emit an interactive volume progress bar with [-] and [+] step buttons."""
    try:
        from amigo.services.screen_pen import push_overlay_event
        vol_val = max(0, min(100, int(level)))

        push_overlay_event({"type": "ink_clear", "all": True})

        bars_mark: dict[str, Any] = {
            "kind": "bars",
            "part": "amigo-vol-bars",
            "title": "Audio Volume",
            "bars": [{"label": "Volume Level", "value": vol_val, "tone": "sky"}],
            "unit": "%",
        }
        btn_down: dict[str, Any] = {
            "kind": "button",
            "part": "act-vol-down",
            "text": "[-] Down 10%",
            "tone": "sky",
        }
        btn_up: dict[str, Any] = {
            "kind": "button",
            "part": "act-vol-up",
            "text": "[+] Up 10%",
            "tone": "teal",
        }

        push_overlay_event({
            "type": "ink",
            "id": "amigo-volume",
            "seg": 0,
            "last": True,
            "fade": float(duration),
            "beats": [
                {
                    "voiced": True,
                    "caption": f"Volume at {vol_val}%",
                    "marks": [bars_mark, {"kind": "sfx", "name": "scan"}],
                },
                {
                    "voiced": False,
                    "caption": "Adjust volume",
                    "marks": [btn_down, btn_up, {"kind": "sfx", "name": "pip"}],
                },
            ],
        })
    except Exception as exc:
        logger.debug("[Interactive Volume] Error emitting: %s", exc)


def emit_interactive_brightness(level: int | float, duration: float = 16.0) -> None:
    """Emit an interactive brightness progress bar with step buttons."""
    try:
        from amigo.services.screen_pen import push_overlay_event
        bright_val = max(0, min(100, int(level)))

        push_overlay_event({"type": "ink_clear", "all": True})

        bars_mark: dict[str, Any] = {
            "kind": "bars",
            "part": "amigo-bright-bars",
            "title": "Display Brightness",
            "bars": [{"label": "Brightness", "value": bright_val, "tone": "amber"}],
            "unit": "%",
        }
        btn_down: dict[str, Any] = {
            "kind": "button",
            "part": "act-bright-down",
            "text": "[-] Dimmer",
            "tone": "amber",
        }
        btn_up: dict[str, Any] = {
            "kind": "button",
            "part": "act-bright-up",
            "text": "[+] Brighter",
            "tone": "lime",
        }

        push_overlay_event({
            "type": "ink",
            "id": "amigo-bright",
            "seg": 0,
            "last": True,
            "fade": float(duration),
            "beats": [
                {
                    "voiced": True,
                    "caption": f"Brightness at {bright_val}%",
                    "marks": [bars_mark, {"kind": "sfx", "name": "scan"}],
                },
                {
                    "voiced": False,
                    "caption": "Adjust brightness",
                    "marks": [btn_down, btn_up, {"kind": "sfx", "name": "pip"}],
                },
            ],
        })
    except Exception as exc:
        logger.debug("[Interactive Brightness] Error emitting: %s", exc)


def emit_interactive_weather(city_name: str, temp: Any, condition: str, humidity: Any, wind: Any, duration: float = 20.0) -> None:
    """Emit a weather card together with an atmospheric condition bar gauge."""
    try:
        from amigo.services.screen_pen import push_overlay_event
        try:
            t_val = int(float(temp or 20))
        except (ValueError, TypeError):
            t_val = 20
        try:
            h_val = int(float(humidity or 50)) if humidity and str(humidity) not in ("0", "--") else 50
        except (ValueError, TypeError):
            h_val = 50
        try:
            w_val = int(float(wind or 10)) if wind and str(wind) not in ("0", "--") else 10
        except (ValueError, TypeError):
            w_val = 10

        push_overlay_event({"type": "ink_clear", "all": True})

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

        push_overlay_event({
            "type": "ink",
            "id": "amigo-weather",
            "seg": 0,
            "last": True,
            "fade": float(duration),
            "beats": [
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
        })
    except Exception as exc:
        logger.debug("[Interactive Weather] Error emitting: %s", exc)


def emit_interactive_reminders(reminders: list[dict], duration: float = 25.0) -> None:
    """Emit interactive checklist of reminders using Roamies native task list."""
    try:
        from amigo.services.screen_pen import push_overlay_event, emit_avatar_act
        items = [f"{r.get('message', 'Reminder')} ({r.get('target_time') or r.get('time', '')})" for r in (reminders or [])[:6]]

        push_overlay_event({"type": "ink_clear", "all": True})

        card_mark: dict[str, Any] = {
            "kind": "card",
            "part": "amigo-rem-card",
            "title": "My Active Reminders",
            "heading": f"{len(reminders)} Reminder{'s' if len(reminders) != 1 else ''}" if reminders else "No Active Reminders",
            "text": f"You have {len(reminders)} scheduled task(s)." if reminders else "Your reminder list is clear.",
            "items": items if items else ["All caught up!"],
            "tone": "amber",
        }

        push_overlay_event({
            "type": "ink",
            "id": "amigo-rem-summary",
            "seg": 0,
            "last": True,
            "fade": float(duration),
            "beats": [{
                "voiced": True,
                "caption": f"You have {len(reminders)} reminders scheduled",
                "marks": [card_mark, {"kind": "sfx", "name": "pop"}, {"kind": "emote", "name": "remind"}],
            }],
        })
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
            push_overlay_event({
                "type": "reminders",
                "today": today_items,
                "later": [],
                "seconds": int(duration),
            })
    except Exception as exc:
        logger.debug("[Interactive Reminders] Error emitting: %s", exc)


# ═══════════════════════════════════════════════════════════════
#  Tool Handlers (Context & Multi-Step Aware)
# ═══════════════════════════════════════════════════════════════

def _tool_web_search(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()

    raw_q = str(params.get("query") or query or "").strip() or query
    resolved_q = rag_engine.resolve_contextual_query(raw_q, conv_history)
    target = clean_search_query(resolved_q.replace("(", "").replace(")", "")) or raw_q
    show_in_browser = bool(params.get("show_in_browser"))

    update_active_state("last_search", {"query": target})
    update_active_state("active_subject", {"name": target, "category": "web_search"})

    # Roamies tool badge microinteraction
    tool_id = None
    try:
        from amigo.services.screen_pen import emit_tool_anim, end_tool_anim
        tool_id = emit_tool_anim("web", label=f"Searching: {target[:24]}", variant="search")
    except Exception:
        pass

    try:
        search_response = search_web(target, max_results=6, fetch_content=False, fast_mode=True)
    except Exception:
        if tool_id:
            try:
                end_tool_anim(tool_id, is_error=True)
            except Exception:
                pass
        return _offline_reply(query, "web search is unavailable"), None, {"status": "offline"}
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(target)}"
    if show_in_browser:
        searchGoogle(target)

    if tool_id:
        try:
            end_tool_anim(tool_id, is_error=not bool(search_response.results))
        except Exception:
            pass

    if search_response.results:
        web_context = format_for_llm(search_response, max_chars=3500)
        prompt_q = resolved_q if resolved_q and resolved_q != raw_q else query
        response = get_ai_response(
            prompt_q,
            web_context=web_context,
            conversation_history=conv_history,
            use_memory=True,
            max_tokens=180,
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
    target = str(params.get("symbol") or params.get("query") or query or "").strip()

    try:
        quote = get_stock_quote(target)
    except Exception:
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

    if not quote and not is_internet_connected():
        return _offline_reply(query, "you cannot check market quotes offline"), None, {"status": "offline"}
    return f"I couldn't find a live stock quote for '{target}'.", None, {"status": "not_found"}


def _tool_open_website(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    raw_url = str(params.get("url") or "").strip()
    title = str(params.get("title") or "").strip()

    state = rag_engine.get_active_state(clean_expired=True) or {}
    last_link_data = state.get("last_link") or state.get("last_search") or {}
    prior_url = str(last_link_data.get("url") or "").strip()

    if prior_url:
        p_parsed = urllib.parse.urlparse(prior_url)
        p_netloc = p_parsed.netloc.lower().replace("www.", "")
        r_parsed = urllib.parse.urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
        r_netloc = r_parsed.netloc.lower().replace("www.", "")

        is_generic_root = not r_parsed.path or r_parsed.path == "/"
        domain_matches = bool(r_netloc and p_netloc and (r_netloc in p_netloc or p_netloc in r_netloc))

        if not raw_url or (domain_matches and is_generic_root and len(p_parsed.path) > 1):
            raw_url = prior_url
            if not title and last_link_data.get("title"):
                title = str(last_link_data["title"])

    has_valid_target = bool(raw_url and ("." in raw_url or "://" in raw_url))
    if not has_valid_target:
        return "Which website would you like me to open?", None, {"status": "missing_url"}

    if re.match(r"^https?://", raw_url, re.I):
        url = raw_url
    elif "://" in raw_url or raw_url.lower().startswith(("javascript:", "file:", "data:")):
        return "I can only open regular web addresses.", None
    else:
        url = f"https://{raw_url}"
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if opened is False:
        if not is_internet_connected():
            return _offline_reply(query, "websites cannot be opened"), None, {"status": "offline"}
        return f"I couldn't open {url} in your browser.", None, {"status": "error"}
    label = title or url
    update_active_state("active_subject", {"name": label, "category": "website", "url": url})
    update_active_state("last_link", {"name": label, "url": url, "title": label})
    return f"Opening {label}.", url, {"url": url, "title": label}


def _tool_play_youtube(params, query, spoken):
    q = (params or {}).get("query", query) or query
    try:
        media_info = resolve_youtube_video(q)
    except Exception:
        media_info = {}
    clean_q = media_info.get("query") or q

    if not media_info.get("url"):
        if not is_internet_connected():
            return _offline_reply(query, "you cannot stream YouTube videos offline"), None, {"status": "offline"}
        return f"I couldn't find a YouTube video for '{clean_q}'.", None

    media_payload = {
        "title": media_info.get("title", clean_q.title()),
        "artist": media_info.get("channel", "YouTube Music"),
        "video_id": media_info.get("video_id", ""),
        "url": media_info.get("url", ""),
        "status": "playing",
        "volume": 75,
    }
    if _media_update_cb:
        _media_update_cb(media_payload)

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
    by_ch = f" by {channel}" if channel and channel not in ("YouTube", "YouTube Music", "") else ""
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
            return f"'{clean_title}'{by_artist} is paused.", None
        return f"Currently playing '{clean_title}'{by_artist}.", None

    try:
        sys_media = os_automation.get_system_media_info()
        if sys_media and sys_media.get("title"):
            clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", sys_media["title"]).strip()
            raw_artist = sys_media.get("artist", "")
            by_artist = f" by {raw_artist}" if raw_artist and raw_artist.lower() not in clean_title.lower() else ""
            status = sys_media.get("status", "playing")
            update_active_state("current_media", {"title": clean_title, "artist": raw_artist, "status": status.lower()})
            if status.lower() == "paused":
                return f"'{clean_title}'{by_artist} is paused.", None
            return f"Currently playing '{clean_title}'{by_artist}.", None
    except Exception:
        pass

    last_played = get_last_played_song()
    if last_played and last_played.get("title"):
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", last_played["title"]).strip()
        raw_artist = last_played.get("artist", "")
        by_artist = f" by {raw_artist}" if raw_artist and raw_artist.lower() not in clean_title.lower() else ""
        if is_playing:
            return f"Currently playing '{clean_title}'{by_artist}.", last_played.get("url")
        return f"'{clean_title}'{by_artist} was played recently.", last_played.get("url")

    state = get_active_state(clean_expired=True) or {}
    media_state = state.get("current_media") or {}
    title = media_state.get("title") or media_state.get("query") or ""
    if title:
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", title).strip()
        artist = media_state.get("artist") or ""
        by_artist = f" by {artist}" if artist and artist.lower() not in clean_title.lower() else ""
        if clean_title:
            return f"Currently playing '{clean_title}'{by_artist}.", media_state.get("url")

    if is_playing:
        return "Audio is currently playing on your PC, but the track name could not be identified.", None

    return "No song or video is currently playing.", None


def _tool_get_time(params, query, spoken):
    tz_name = str((params or {}).get("timezone") or "").strip() if isinstance(params, dict) else ""
    now = datetime.datetime.now()
    where = ""
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo(tz_name))
            where = f" in {tz_name.split('/')[-1].replace('_', ' ')}"
        except Exception:
            return f"I couldn't look up the time zone '{tz_name}', so here is your local time: {now.strftime('%I:%M %p').lstrip('0')}.", None
    return f"It is currently {now.strftime('%I:%M %p').lstrip('0')}{where}.", None


def _tool_get_date(params, query, spoken):
    now = datetime.datetime.now()
    return f"Today is {now.strftime('%A, %B')} {now.day}, {now.year}.", None


def _tool_time_date(params, query, spoken):
    action = str((params or {}).get("action") or (params or {}).get("kind") or "both").strip().lower()
    if action == "time":
        return _tool_get_time(params, query, spoken)
    if action == "date":
        return _tool_get_date(params, query, spoken)
    now = datetime.datetime.now()
    return f"It is {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B')} {now.day}, {now.year}.", None


def _tool_get_datetime(params, query, spoken):
    kind = str((params or {}).get("kind") or "time").strip().lower()
    if kind == "date":
        return _tool_get_date(params, query, spoken)
    if kind == "both":
        return _tool_time_date(params, query, spoken)
    return _tool_get_time(params, query, spoken)


_WEATHER_CACHE: collections.OrderedDict[str, tuple[float, dict]] = collections.OrderedDict()
_WEATHER_TTL_SECONDS = 300.0


def _cached_weather_data(city: str) -> dict:
    key = (city or "").strip().lower()
    hit = _WEATHER_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _WEATHER_TTL_SECONDS:
        _WEATHER_CACHE.move_to_end(key)
        return hit[1]
    data = get_weather_data(city or "")
    if isinstance(data, dict) and data:
        _WEATHER_CACHE[key] = (time.time(), data)
        if len(_WEATHER_CACHE) > 32:
            _WEATHER_CACHE.popitem(last=False)
        return data
    return {}


def _tool_get_weather(params, query, spoken):
    params = params if isinstance(params, dict) else {}

    city = str(params.get("city") or "").strip()
    if not city:
        state = get_active_state(clean_expired=True) or {}
        subj = state.get("active_subject") or {}
        if subj.get("category") == "weather" and subj.get("name"):
            city = subj["name"]

    w_data = _cached_weather_data(city)
    temp = w_data.get("temp_c") or w_data.get("temperature") if isinstance(w_data, dict) else None
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
        return result, None

    try:
        result = weather_command(city if city else query)
    except Exception:
        result = ""
    if result:
        emit_interactive_weather(city or "Local", "--", "Report", 50, 10)
        return result, None
    if not is_internet_connected():
        return _offline_reply(query, "live weather cannot be fetched"), None, {"status": "offline"}
    return f"I couldn't fetch weather information for {city or 'your area'}.", None


def is_app_already_running(app_name: str) -> bool:
    target = (app_name or "").lower().strip()
    if not target:
        return False
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower().replace(".exe", "")
            if target == name or bool(re.search(rf"\b{re.escape(target)}\b", name)):
                return True
    except Exception:
        pass
    return False


def _tool_open_app(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    app_name = (params.get("name") or params.get("app") or params.get("app_name") or params.get("target") or params.get("query") or "").strip()
    if not app_name:
        return "Which application would you like to open?", None

    clean_target = app_name.strip()
    if not is_known_app(clean_target) and not is_app_already_running(clean_target):
        return f"I couldn't find an app called '{clean_target.title()}' installed on your PC.", None
    if open_windows_app(clean_target):
        update_active_state("active_app", {"name": clean_target})
        return f"Opened {clean_target.title()}.", None, {"status": "completed", "app": clean_target}

    if is_app_already_running(clean_target):
        update_active_state("active_app", {"name": clean_target})
        return f"{clean_target.title()} is already open.", None, {"status": "completed", "app": clean_target}

    return f"I couldn't find an app called '{clean_target.title()}' installed on your PC.", None, {"status": "completed", "query": clean_target}


def _tool_open_folder(params, query, spoken):
    folder_name = str((params or {}).get("name") or "").strip()
    if not folder_name or not is_known_folder(folder_name):
        return "Which folder would you like to open?", None
    _, msg = open_folder(folder_name)
    return msg or spoken, None


def _tool_find_file(params, query, spoken):
    """Unified fast filename + semantic file finder."""
    q = str((params or {}).get("query") or query or "").strip()
    matches, summary = find_files(q)
    file_results = []
    seen_paths = set()

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
        for r in rag_engine.search_files_by_context(q, top_k=5):
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
        update_active_state("found_files", [f["path"] for f in file_results[:5]])
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
        for i, f in enumerate(file_results[:5], 1):
            clean_t = os.path.splitext(f["name"])[0].replace("_", " ").replace("-", " ").title()
            lines.append(f"{i}. {clean_t} ({f['extension']})")
        return "\n".join(lines), None, {"file_results": file_results}

    return summary or f"No files found matching '{q}'.", None, {"file_results": []}


def _tool_take_screenshot(params, query, spoken):
    try:
        from amigo.services.screen_vision import capture_screen_image
        capture_screen_image("amigo_screenshot.png")
        return "Screenshot saved.", None
    except Exception as e:
        return f"Failed to take screenshot: {e}", None


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
            "window_title": win_title,
            "screenshot": res.get("screenshot_path", "amigo_screenshot.png"),
            "text_snippet": screen_text[:300],
            "screen_text": screen_text,
        }
    except Exception:
        return "Could not inspect the screen right now.", None


def _tool_read_window(params, query, spoken):
    """List controls, text, and structure of the front or named window using native `hands.ui`."""
    params = params if isinstance(params, dict) else {}
    target = str(params.get("target") or params.get("app") or "front").strip()
    if not target or target.lower() in ("front", "current", "active"):
        t = "front"
    else:
        t = f"app:{target}"
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
    header = f"{app_name}{f' — {title}' if title else ''}"
    lines = [header]
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
        lines.append("\nVisible text: " + " | ".join(str(t) for t in texts[:15]))
    if elsewhere:
        lines.append("\nElsewhere (scrollable): " + " | ".join(str(e) for e in elsewhere[:10]))
    return "\n".join(lines), None, res


def _tool_memory_recall(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "").strip().lower()
    conv_history = _get_conv_history()
    q = str(params.get("query") or query or "").strip()
    q_low = q.lower()

    fact = str(params.get("fact") or "").strip()
    if action == "store" or fact:
        if not fact:
            fact = q
        rag_engine.add_user_fact(fact, category="user_memory")
        return f"I've remembered that: {fact}.", None

    recalled_facts = []
    recalled = rag_engine.search(
        q,
        target_collections=[rag_engine.USER_FACTS, rag_engine.CONVERSATIONS],
        top_k=5,
        query_type="conversation_recall",
        conversation_history=conv_history,
    )
    for r in recalled:
        if r.get("score", 0) >= 0.40 and r.get("text"):
            recalled_facts.append(r["text"])

    prefs = rag_engine.load_profile().get("preferences", {})
    for k, v in prefs.items():
        if str(k).lower() in q_low or (str(v).strip() and str(v).lower() in q_low):
            recalled_facts.append(f"{k}: {v}")

    if recalled_facts:
        mem_context = "Remembered User Facts & Conversations:\n" + "\n".join(f"- {f}" for f in recalled_facts)
        return get_ai_response(q, doc_context=mem_context, conversation_history=conv_history), None, {"recalled_facts": recalled_facts[:3]}

    return "I don't have anything saved about that yet.", None


def _tool_ask_document(params, query, spoken, conversation_history=None):
    """Unified RAG Q&A handler with full follow-up context, cross-corpus fallback, and Roamies overlay card."""
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history(conversation_history)
    filepath = str(params.get("filepath") or params.get("filename") or params.get("file") or "").strip()
    t_idx = params.get("target_index")
    question = str(params.get("question") or params.get("query") or query or "").strip() or query

    target_path = None
    if t_idx is not None:
        target_path = resolve_document_path("", target_index=t_idx)
    elif filepath:
        target_path = resolve_document_path(filepath)
    else:
        resolved_q = rag_engine.resolve_contextual_query(question, conv_history)
        if resolved_q != question:
            act_f = (rag_engine.get_active_state(clean_expired=False) or {}).get("active_file")
            if isinstance(act_f, dict) and act_f.get("path") and os.path.exists(act_f["path"]):
                target_path = act_f["path"]

    search_q = f"{filepath} {question}".strip() if (filepath and not target_path) else question

    # Live document tool badge
    tool_id = None
    try:
        from amigo.services.screen_pen import emit_tool_anim, end_tool_anim
        fname_label = os.path.basename(target_path) if target_path else "Documents"
        tool_id = emit_tool_anim("file", label=f"Reading: {fname_label[:20]}", variant="read")
    except Exception:
        pass

    # 1. Primary: Grounded document evidence with passage citations
    evidence, sources = rag_engine.build_document_evidence(
        search_q,
        top_k=5,
        conversation_history=conv_history,
        filepath=target_path if target_path and os.path.exists(target_path) else None,
    )

    # 2. Fallback: If scoped to a single active_file and nothing matched, search across all indexed documents
    if (not evidence or not sources) and target_path:
        evidence, sources = rag_engine.build_document_evidence(
            search_q,
            top_k=5,
            conversation_history=conv_history,
            filepath=None,
        )

    if tool_id:
        try:
            end_tool_anim(tool_id, is_error=not bool(evidence and sources))
        except Exception:
            pass

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
        max_tokens=650,
    )
    source_list = "; ".join(
        f"[{source['id']}] {source['file']} — {source['location']}"
        for source in sources
    )
    primary_file = sources[0].get("file", "Document")
    card_sources = [{"file": str(s.get("file", "")), "location": str(s.get("location", ""))} for s in sources[:4]]
    emit_roamies_card(
        title=f"Document · {primary_file}",
        heading=question[:36],
        text=response[:380],
        items=[f"{s['file']} ({s['location']})" for s in sources[:4]],
        tone="teal",
        duration=22.0,
        sources=card_sources,
    )
    full_response = f"{response.rstrip()}\n\nSources: {source_list}".strip()
    return full_response, None, {"status": "success", "doc_context_used": True, "sources": sources}


def _tool_summarize_document(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()
    filepath = str(params.get("filepath") or params.get("path") or params.get("filename") or "").strip()
    t_idx = params.get("target_index")
    target_path = resolve_document_path(filepath, target_index=t_idx) if (filepath or t_idx is not None) else None
    if not target_path:
        act_f = (rag_engine.get_active_state(clean_expired=False) or {}).get("active_file")
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
                max_tokens=650,
            )
            emit_roamies_card(
                title=f"Summary · {fname}",
                heading=fname[:36],
                text=summary[:380],
                tone="teal",
                duration=22.0,
            )
            return summary, None, {"status": "completed", "file": target_path}

    return "Which document would you like me to summarize?", None


def _tool_search_knowledge(params, query, spoken):
    conv_history = _get_conv_history()
    q = (params or {}).get("query", query).strip()
    ctx = rag_engine.build_rag_context(q, top_k=5, conversation_history=conv_history, for_voice=False)
    return get_ai_response(q, doc_context=ctx, conversation_history=conv_history, is_voice=False, max_tokens=650), None


def _tool_chat(params, query, spoken, conversation_history=None):
    """Conversational reply with clean multi-turn dialogue context."""
    params = params if isinstance(params, dict) else {}
    doc_context = str(params.get("context") or params.get("doc_context") or "").strip()
    direct_reply = (
        spoken
        or params.get("reply")
        or params.get("response")
        or params.get("question")
        or params.get("message")
        or ""
    ).strip()

    conv_history = _get_conv_history(conversation_history)

    is_repetition = False
    if direct_reply and conv_history:
        last_turn = conv_history[-1]
        last_assistant = str(
            last_turn.get("assistant") or last_turn.get("assistant_reply") or last_turn.get("response") or ""
        ).strip()
        if last_assistant and len(direct_reply) > 10:
            if direct_reply in last_assistant or last_assistant in direct_reply:
                is_repetition = True

    is_complete = bool(direct_reply and direct_reply.rstrip()[-1:] in ('.', '!', '?', '"', "'", '`', '}', '\n'))
    if direct_reply and not doc_context and len(direct_reply) >= 3 and is_complete and not is_repetition:
        return direct_reply, None, {"doc_context_used": False}

    if not query or not query.strip():
        if direct_reply and not is_repetition:
            return direct_reply, None

    # Optional avatar mood gesture passed from LLM intent
    mood = str(params.get("mood") or "").strip().lower()
    if mood:
        try:
            from amigo.services.screen_pen import emit_avatar_act
            emit_avatar_act(mood)
        except Exception:
            pass

    ai_reply = get_ai_response(query, doc_context=doc_context, conversation_history=conv_history)
    return ai_reply, None, ({"doc_context_used": True} if doc_context else {})


# ── OS & Media Controls ────────────────────────────────────────

def _tool_press_key(params, query, spoken):
    keys = (params or {}).get("keys", "")
    if keys:
        os_automation.press_shortcut(keys)
    return (f"Pressed {keys}." if keys else "Done."), None


def _tool_click_screen(params, query, spoken):
    import pyautogui
    params = params if isinstance(params, dict) else {}
    x, y, button = params.get("x"), params.get("y"), params.get("button", "left")
    try:
        if x is not None and y is not None:
            pyautogui.click(int(x), int(y), button=button)
            return f"Clicked at ({x}, {y}).", None
        pyautogui.click(button=button)
        return "Clicked.", None
    except Exception as e:
        return f"Click error: {e}", None


_SCREEN_ACT_ACTIONS = {"click": "click", "fill": "fill", "type": "type", "press": "press", "scroll": "scroll"}
_SCROLL_DIRS = {"up": "up", "down": "down", "left": "left", "right": "right"}


def _tool_screen_act(params, query, spoken):
    """Execute a structured on-screen UI action via `hands`."""
    params = params if isinstance(params, dict) else {}
    app = str(params.get("app") or "").strip()

    raw_steps = params.get("steps")
    if isinstance(raw_steps, str) and raw_steps.strip().startswith("["):
        try:
            raw_steps = json.loads(raw_steps)
        except Exception:
            pass

    if isinstance(raw_steps, list) and raw_steps:
        if not app:
            raw_target = str(params.get("target") or "").strip()
            if raw_target and raw_target.lower() not in ("front", "current", "active", "screen"):
                app = raw_target
        plan_steps = [s for s in raw_steps if isinstance(s, dict)]
    else:
        action = str(params.get("action") or params.get("do") or "").strip().lower()
        target = str(params.get("target") or params.get("field") or params.get("text") or "").strip()
        text = str(params.get("text") or params.get("option") or params.get("value") or "").strip()

        if action in ("click", "click_text"):
            if not target:
                return "Which button or element on screen should I click?", None
            step = {"do": "click", "id": int(target)} if target.isdigit() else {"do": "click_text", "text": target}
        elif action == "fill":
            if not target or not text:
                return "Which field should I fill and what text should I enter?", None
            step = {"do": "fill", "field": target, "text": text}
        elif action in ("type", "type_text"):
            if not text:
                return "What text should I type?", None
            step = {"do": "type", "text": text}
        elif action in ("press", "key", "press_key"):
            keys = text or target
            if not keys:
                return "Which key or shortcut should I press?", None
            step = {"do": "key", "key": keys}
        elif action in ("scroll", "scroll_down", "scroll_up"):
            direction = _SCROLL_DIRS.get(target.lower(), "down")
            step = {"do": "scroll", "direction": direction}
        elif action == "choose":
            step = {"do": "choose", "field": target, "option": text}
        elif action == "wait":
            try:
                secs = float(text or target or 1.0)
            except ValueError:
                secs = 1.0
            step = {"do": "wait", "seconds": secs}
        else:
            return "Which on-screen action should I perform: click, fill, type, press, scroll, or choose?", None
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

    plan = {"steps": plan_steps}
    if app and app.lower() not in ("front", "current", "active", "screen"):
        plan["app"] = app
    res = hands.act(plan, on_progress=_on_hands_progress)
    if res.get("ok"):
        done = res.get("done")
        summary = ", ".join(done) if isinstance(done, list) and done else "Action completed"
        return f"{summary}.", None, {"hands_used": True, "plan": plan}
    return str(res.get("error") or "Could not complete on-screen action."), None, {"hands_used": True, "error": res.get("error")}


_audio_playing_cache = {"status": False, "time": 0.0}


def _is_system_audio_playing() -> bool:
    now = time.time()
    if now - _audio_playing_cache["time"] < 1.5:
        return _audio_playing_cache["status"]
    status = False
    try:
        import pythoncom
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        pythoncom.CoInitialize()
        try:
            for s in AudioUtilities.GetAllSessions():
                if s.Process:
                    try:
                        if s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() > 0.0005:
                            status = True
                            break
                    except Exception:
                        pass
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
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
        except Exception:
            pass
        _live_media_cache = res
        _live_media_cache_time = time.time()
    finally:
        _live_media_refreshing = False


def _get_live_media_title_from_windows(wait_if_cold: bool = False) -> tuple[str, str, str] | None:
    """Non-blocking cached media lookup (never stalls the router thread)."""
    global _live_media_cache, _live_media_cache_time, _live_media_refreshing
    now = time.time()
    if (now - _live_media_cache_time) < 15.0 and _live_media_cache is not None:
        return _live_media_cache
    with _live_media_lock:
        if not _live_media_refreshing:
            _live_media_refreshing = True
            threading.Thread(target=_fetch_live_media_worker, daemon=True).start()
    if _live_media_cache is None and wait_if_cold:
        end = time.time() + 0.5
        while _live_media_refreshing and time.time() < end:
            time.sleep(0.02)
    return _live_media_cache


def _tool_stop(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception:
        pass
    if _is_system_audio_playing():
        try:
            os_automation.play_pause_media()
        except Exception:
            pass
    return "", None


def _tool_stop_speaking(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception:
        pass
    return "", None


def _tool_blocked(params, query, spoken):
    return "", None


def _tool_pause_media(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception:
        pass
    live_media = _get_live_media_title_from_windows()
    if live_media and live_media[2].lower() == "paused":
        return "Media is already paused.", None
    try:
        os_automation.play_pause_media()
    except Exception:
        pass
    if _media_update_cb:
        _media_update_cb({"status": "paused"})
    media = (get_active_state(clean_expired=True) or {}).get("current_media")
    if isinstance(media, dict):
        update_active_state("current_media", {**media, "status": "paused"})
    return "Media paused.", None


def _tool_play_media(params, query, spoken):
    live_media = _get_live_media_title_from_windows()
    if live_media and live_media[2].lower() == "playing":
        return "Media is already playing.", None
    try:
        os_automation.play_pause_media()
    except Exception:
        pass
    if _media_update_cb:
        _media_update_cb({"status": "playing"})
    media = (get_active_state(clean_expired=False) or {}).get("current_media")
    if isinstance(media, dict):
        update_active_state("current_media", {**media, "status": "playing"})
    return "Media resumed.", None


def _mark_media_stale():
    media = (get_active_state(clean_expired=False) or {}).get("current_media")
    if isinstance(media, dict) and media:
        update_active_state("current_media", {**media, "stale": True, "status": "playing"})


def _tool_next_track(params, query, spoken):
    os_automation.next_track()
    _mark_media_stale()
    return "Next track.", None


def _tool_prev_track(params, query, spoken):
    os_automation.prev_track()
    _mark_media_stale()
    return "Previous track.", None


def _tool_media_control(params, query, spoken):
    action = str((params or {}).get("action") or "").strip().lower()
    target = _MEDIA_ACTION_MAP.get(action)
    if target and target in UI_TOOL_HANDLERS:
        return UI_TOOL_HANDLERS[target](params, query, spoken)
    return "Which media action would you like: play, pause, next, previous, or current status?", None


def get_pending_confirmation():
    global _PENDING_CONFIRMATION
    if _PENDING_CONFIRMATION:
        start_time = _PENDING_CONFIRMATION.get("timer_started") or _PENDING_CONFIRMATION.get("created")
        if start_time and (time.time() - start_time) > _PENDING_TTL_SECONDS:
            _PENDING_CONFIRMATION = None
    return _PENDING_CONFIRMATION


def start_pending_confirmation_timer():
    """Starts/resets the 30-second TTL countdown after the confirmation question finishes being spoken."""
    global _PENDING_CONFIRMATION
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
    name = str(params.get("name") or params.get("app_name") or params.get("app") or "").strip()
    if not name:
        os_automation.window_action("close_window")
        return "Window closed.", None
    closed = os_automation.close_app(name)
    return (f"Closed {name.title()}." if closed else f"{name.title()} is not currently running."), None


def _tool_window_management(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = params.get("action", "")
    app_name = params.get("app_name", "") or params.get("name", "")
    if action in ("close_window", "close_app"):
        return _tool_close_app({"app_name": app_name}, query, spoken)
    if action:
        os_automation.window_action(action)
    return "Done.", None


def _tool_calculate(params, query, spoken):
    expr = str((params or {}).get("expression") or query or "").strip()
    if expr:
        try:
            res = Calc(expr)
            if res is not None:
                return f"The answer is {res}.", None
        except Exception:
            pass
    return get_ai_response(f"Solve this concisely: {expr or query}", use_memory=False), None


def _tool_clarification(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    candidates = params.get("candidate_tools", [])
    q = params.get("query", query)
    custom_msg = params.get("message")
    spoken_reply = custom_msg or f"I'm not completely sure what you'd like to do with '{q}'. Please choose an option below or clarify."
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
        return default_msg, None
    return _handler


def _tool_empty_recycle_bin(params, query, spoken):
    confirm = _require_confirmation("Empty recycle bin", params, query, "Emptying recycle bin")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.empty_recycle_bin()
    return "Recycle bin emptied.", None


def _tool_lock_pc(params, query, spoken):
    confirm = _require_confirmation("Lock PC", params, query, "Locking PC")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.lock_pc()
    return "Locking your PC.", None


def _tool_sleep_pc(params, query, spoken):
    confirm = _require_confirmation("Sleep PC", params, query, "Putting system to sleep")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.sleep_pc()
    return "Putting system to sleep.", None


def _tool_restart_pc(params, query, spoken):
    confirm = _require_confirmation("Restart PC", params, query, "Restarting PC in 30 seconds")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.restart_pc(30)
    return "Restarting in 30 seconds.", None


def _tool_system_control(params, query, spoken):
    action = (params.get("action", "") if isinstance(params, dict) else "").lower()
    target = _POWER_ACTION_MAP.get(action)
    if target and target != "system_control" and target in UI_TOOL_HANDLERS:
        return UI_TOOL_HANDLERS[target](params, query, spoken)
    if action in ("shutdown", "shutdown_pc"):
        confirm = _require_confirmation("Shutdown PC", params, query, "Shutting down PC")
        if confirm[2].get("requires_confirmation"):
            return confirm
        shutdown_fn = getattr(os_automation, "shutdown_pc", None)
        if callable(shutdown_fn):
            shutdown_fn(30)
        else:
            os.system("shutdown /s /t 30")
        return "Shutting down in 30 seconds.", None
    if action == "cancel_shutdown":
        os_automation.cancel_shutdown()
        return "Shutdown cancelled.", None
    if action in ("empty_recycle_bin", "empty_bin", "recycle_bin"):
        return _tool_empty_recycle_bin(params, query, "Emptying recycle bin")
    return f"Unknown system control action: {action}", None


# ── Document Path Resolution ───────────────────────────────────

def resolve_document_path(query_or_target: str, target_index: int | None = None) -> str | None:
    state = get_active_state(clean_expired=False) or {}
    active_f = state.get("active_file")
    active_path = active_f.get("path") if isinstance(active_f, dict) else None
    active_ok = bool(active_path and os.path.exists(active_path))

    found_list = state.get("found_files", [])
    if isinstance(found_list, dict):
        found_list = found_list.get("items", [])

    # Ordinals index convention: 0 = first, 1 = second, -1 = last
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

    if not query_or_target or not isinstance(query_or_target, str):
        return active_path if active_ok else None

    raw = query_or_target.strip()
    if os.path.isfile(raw):
        return raw

    search_terms = [raw] if len(raw) >= 2 else []

    for term in search_terms:
        matches, _ = find_files(term)
        if matches:
            update_active_state("active_file", {"path": matches[0], "name": os.path.basename(matches[0])})
            return matches[0]

    for term in search_terms:
        if len(term) >= 3:
            results = rag_engine.search_files_by_context(term, top_k=1)
            if results and results[0].get("score", 0) > 0.35:
                fpath = results[0].get("filepath")
                if fpath and os.path.exists(fpath):
                    update_active_state("active_file", {"path": fpath, "name": os.path.basename(fpath)})
                    return fpath

    # Do NOT fall back to an unrelated active_file when an explicit target did not match
    return None


def _tool_file_action(params, query, spoken, default_action="open"):
    params = params if isinstance(params, dict) else {}
    target = str(params.get("path") or params.get("name") or "").strip()
    action = params.get("action") or default_action
    t_idx = params.get("target_index")

    if t_idx is None and target and not os.path.isfile(target):
        matches, _ = find_files(target)
        if len(matches) > 1 and os.path.basename(matches[0]).lower() != target.lower():
            update_active_state("found_files", matches[:5])
            lines = [f"{i}. {os.path.basename(p)}" for i, p in enumerate(matches[:5], 1)]
            return "I found multiple matching files:\n" + "\n".join(lines) + "\nWhich one would you like to open?", None

    target_path = resolve_document_path(target, target_index=t_idx)

    if not (target_path and os.path.exists(target_path)):
        return "Which file would you like to open?", None

    update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
    ok, msg = execute_file_action(target_path, action)
    if not ok:
        return msg or f"I couldn't {str(action).replace('_', ' ')} that file.", None
    clean_name = os.path.splitext(os.path.basename(target_path))[0].replace("_", " ").replace("-", " ").title()
    return (f"Opened {clean_name}." if action == "open" else msg or f"Done with {clean_name}."), None


def _make_file_handler(default_action):
    def _handler(params, query, spoken):
        return _tool_file_action(params, query, spoken, default_action=default_action)
    return _handler


def _tool_show_images(params, query, spoken):
    target = (params or {}).get("query", query)
    return spoken or "Showing image results.", f"https://www.google.com/search?q={urllib.parse.quote(target)}"


def _tool_search_and_type(params, query, spoken):
    confirm = _require_confirmation("Search and type", params, query, spoken or "Searching and typing")
    if confirm[2].get("requires_confirmation"):
        return confirm
    os_automation.search_and_type((params or {}).get("text", ""))
    return spoken, None


def _tool_new_tab(params, query, spoken):
    os_automation.new_tab((params or {}).get("url", ""))
    return spoken, None


def _level_result(res, default_msg):
    if isinstance(res, (tuple, list)):
        return res[-1]
    return res if isinstance(res, str) else default_msg


def _tool_set_volume(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "").strip().lower()
    level = params.get("level")

    if action == "mute":
        os_automation.mute()
        emit_interactive_volume(0)
        return "Audio muted.", None
    if action == "unmute":
        unmute_fn = getattr(os_automation, "unmute", None)
        if callable(unmute_fn):
            unmute_fn()
        else:
            os_automation.mute()
        emit_interactive_volume(os_automation.get_volume() or 50)
        return "Audio unmuted.", None
    if action == "volume_up":
        os_automation.volume_up()
        emit_interactive_volume(os_automation.get_volume() or 65)
        return "Volume increased.", None
    if action == "volume_down":
        os_automation.volume_down()
        emit_interactive_volume(os_automation.get_volume() or 45)
        return "Volume decreased.", None
    if action == "set_volume" or (not action and level is not None):
        try:
            val = int(level)
            if not (0 <= val <= 100):
                return "What volume level would you like, from 0 to 100?", None
            level = val
        except (ValueError, TypeError):
            return "What volume level would you like, from 0 to 100?", None
    elif level in (None, ""):
        return "What volume level would you like, from 0 to 100?", None
    emit_interactive_volume(level)
    return _level_result(os_automation.set_volume(level), f"Volume set to {level} percent."), None


def _tool_set_brightness(params, query, spoken):
    level = (params or {}).get("level")
    try:
        val = int(level)
        if not (0 <= val <= 100):
            return "What brightness level would you like, from 0 to 100?", None
        level = val
    except (ValueError, TypeError):
        return "What brightness level would you like, from 0 to 100?", None
    emit_interactive_brightness(level)
    return _level_result(os_automation.set_brightness(level), f"Brightness set to {level} percent."), None


def _tool_open_settings(params, query, spoken):
    s = str((params or {}).get("setting") or (params or {}).get("name") or "").strip().lower()
    if _ui_broadcast_cb:
        try:
            if s in ("close", "hide", "dismiss"):
                _ui_broadcast_cb("close_settings", {})
                return "Closed settings.", None
            _ui_broadcast_cb("open_settings", {"setting": s})
        except Exception:
            pass
    if s == "close":
        return "Closed settings.", None
    return open_setting(s or "home"), None


def _tool_control_windows_system(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    category = str(params.get("category") or params.get("action") or "").strip().lower()
    enabled = params.get("enabled")
    mode = params.get("mode")
    level = params.get("level")

    try:
        from amigo.services.os_automation import control_windows_system
        res = control_windows_system(category=category, enabled=enabled, mode=mode, level=level)
        if isinstance(res, tuple):
            return (res[1] or "Which system power setting would you like to adjust?"), None
        msg = res.get("message")
        if not msg:
            if res.get("status") in ("success", "already_connected", "already_on", "already_off"):
                msg = f"{category.capitalize()} status updated successfully."
            else:
                msg = res.get("details") or res.get("code") or f"Could not adjust {category}."
        return msg, None, res
    except Exception as exc:
        logger.error("[Control Windows System Error]: %s", exc)
        return f"Could not adjust system {category}: {exc}", None


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
        else str(res.get("error") or "Could not draw guide.")
    )
    return msg, None, res


def _tool_screen_pen_annotate(params, query, spoken):
    """Pure schema-driven visual diagram & screen annotation dispatcher (zero hardcoded topic/keyword rules)."""
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "").strip().lower()
    x = params.get("x")
    y = params.get("y")
    x2 = params.get("x2")
    y2 = params.get("y2")
    label = params.get("label")
    topic = str(params.get("topic") or "").strip()
    beats = params.get("beats")
    data = params.get("data")
    color = params.get("color", "teal")
    duration = float(params.get("duration", 12.0))
    normalized = bool(params.get("normalized", True))

    if not action:
        action = "diagram" if (topic or data or beats) else "circle"
    if not topic and (label or query):
        topic = str(label or query)

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
                else str(res.get("error") or "Could not draw guide.")
            )
            return msg, None, res

        res = screen_pen_annotate(
            action=action, x=x, y=y, x2=x2, y2=y2,
            label=label, color=color, duration=duration, normalized=normalized,
            topic=topic, data=data, beats=beats,
        )
        if res.get("status") == "success":
            if res.get("summary"):
                msg = res["summary"]
            else:
                msg = f"Highlighted on screen with {res.get('drawn', action)}." if not label else f"Marked '{label}' on screen."
        else:
            msg = str(
                res.get("error")
                or (f"Could not find '{label}' on screen." if label else f"Screen pen action {action} could not be completed.")
            )
        return msg, None, res
    except Exception as exc:
        logger.error("[Screen Pen Annotate Error]: %s", exc)
        return f"Could not annotate screen: {exc}", None


def _tool_change_setting(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    s = str(params.get("setting") or "").strip()
    if not s:
        return "Which setting would you like to change?", None
    val = params.get("value")
    confirmed = bool(params.get("confirmed", False))

    try:
        from amigo.services.os_settings import change_setting, get_setting_state
        ok, msg = change_setting(s, value=val, confirmed=confirmed)
        if not ok and not confirmed:
            return msg, None, {"requires_confirmation": True, "action_type": "change_setting", "setting": s, "value": val}
        _, is_on, _ = get_setting_state(s)
        emit_interactive_setting(s, msg, bool(is_on))
        return msg, None
    except Exception as exc:
        logger.error("[Change Setting Error]: %s", exc)
        return f"Could not change setting: {exc}", None


def _tool_get_setting(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    s = str(params.get("setting") or "").strip()
    if not s:
        return "Which setting would you like to check?", None
    try:
        from amigo.services.os_settings import get_setting_state
        ok, is_on, msg = get_setting_state(s)
        emit_interactive_setting(s, msg, bool(is_on))
        return msg, None
    except Exception as exc:
        logger.error("[Get Setting Error]: %s", exc)
        return f"Could not read setting: {exc}", None


def _tool_open_history(params, query, spoken):
    """Broadcast open/close history drawer events to the React UI."""
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "open").strip().lower()
    if _ui_broadcast_cb:
        try:
            if action in ("close", "hide"):
                _ui_broadcast_cb("close_history", {})
                return "Closed history.", None
            _ui_broadcast_cb("open_history", {})
        except Exception:
            pass
    return "Opened history.", None


def _tool_system_status(params, query, spoken):
    return os_automation.get_system_status().get("summary", "System status checked."), None


def _tool_set_timer(params, query, spoken):
    msg = handle_set_timer(params, query)
    parsed_secs = (
        parse_relative_seconds(str(params.get("query") or ""))
        or parse_relative_seconds(query or "")
        or parse_relative_seconds(str(params.get("duration") or params.get("seconds") or ""))
        or 0
    )
    label = str(params.get("label") or "Timer").strip()
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
    except Exception:
        pass
    return msg, None


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
    emit_roamies_card(
        title="Reminder Update",
        heading="Reminder Cancelled",
        text=res_msg,
        tone="coral",
        duration=14.0,
    )
    return res_msg, None


def _tool_snooze_reminder(params, query, spoken):
    dur_secs = 600.0
    if isinstance(params, dict):
        if "duration_seconds" in params:
            try:
                dur_secs = float(params["duration_seconds"])
            except (ValueError, TypeError):
                pass
        elif "duration" in params:
            parsed = parse_relative_seconds(str(params["duration"]))
            if parsed:
                dur_secs = parsed
    msg = handle_snooze_reminder(params, duration_seconds=dur_secs)
    reminders = get_active_data().get("reminders", [])
    emit_interactive_reminders(reminders)
    emit_roamies_card(
        title="Reminder Snoozed",
        heading="Snoozed",
        text=msg,
        tone="sky",
        duration=14.0,
    )
    return msg, None, {"cards": [], "reminders": reminders}


def _tool_start_focus_mode(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    minutes = int(params.get("duration_minutes") or 30)
    goal = str(params.get("goal") or params.get("task") or "Deep Work").strip()
    from amigo.core.proactive_intelligence import start_focus_session
    msg = start_focus_session(duration_minutes=minutes, goal=goal)
    emit_roamies_card(
        title="Focus Mode",
        heading=f"Focus: {goal}"[:36],
        text=f"Focus session active for {minutes} minutes. Amigo will keep distractions away and give gentle nudges.",
        items=[f"Goal: {goal}", f"Duration: {minutes} minutes", "Notifications quieted"],
        tone="teal",
        duration=20.0,
    )
    return msg, None, {"cards": []}


def _tool_morning_briefing(params, query, spoken):
    from amigo.core.proactive_intelligence import generate_morning_briefing
    summary, items = generate_morning_briefing()
    emit_roamies_card(
        title="Morning Briefing",
        heading="Today's Overview",
        text=summary,
        items=items,
        tone="amber",
        duration=25.0,
    )
    return summary, None, {"cards": []}


def _tool_exit(params, query, spoken):
    return "Goodbye!", None


# ── Email & Calendar Handlers ──────────────────────────────────

def _tool_read_emails(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "").strip().lower()
    if action == "unread":
        return _tool_unread_emails(params, query, spoken)
    if action == "search" or (not action and params.get("query")):
        return _tool_search_emails(params, query, spoken)
    try:
        from amigo.services.mail_integration import get_email_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available.", None
        summary = get_email_summary_text(int(params.get("count") or 5))
        return get_ai_response(f"Summarize these emails naturally:\n{summary}", use_memory=False), None
    except Exception:
        return "Could not read emails.", None


def _tool_search_emails(params, query, spoken):
    conv_history = _get_conv_history()
    q = (params or {}).get("query", query).strip()
    try:
        from amigo.services.mail_integration import is_outlook_available, search_emails
        if is_outlook_available():
            results = search_emails(q, max_results=5)
            if results:
                lines = [f"{i}. From: {e['sender']} | Subject: {e['subject']}" for i, e in enumerate(results, 1)]
                return f"Found {len(results)} email(s) matching '{q}':\n" + "\n".join(lines), None
    except Exception:
        pass
    if rag_ctx := rag_engine.build_rag_context(query or q, top_k=5, conversation_history=conv_history):
        return get_ai_response(query or q, doc_context=rag_ctx, conversation_history=conv_history), None
    return f"No emails found matching '{q}'.", None


def _tool_unread_emails(params, query, spoken):
    try:
        from amigo.services.mail_integration import get_unread_count, get_unread_emails, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available.", None
        count = get_unread_count()
        if count <= 0:
            return "You have no unread emails.", None
        emails = get_unread_emails(min(count, 5))
        lines = [f"You have {count} unread email{'s' if count > 1 else ''}."]
        lines.extend(f"  {i}. From {e['sender']}: {e['subject']}" for i, e in enumerate(emails, 1))
        return "\n".join(lines), None
    except Exception:
        return "Could not check unread emails.", None


def _tool_draft_email(params, query, spoken):
    try:
        from amigo.services.mail_integration import draft_email
        params = params if isinstance(params, dict) else {}
        to, subject = params.get("to", "").strip(), params.get("subject", "").strip()
        prompt = str(params.get("prompt") or query or "").strip()
        body = get_ai_response(
            f"Write a professional email. Context: {prompt}. To: {to or 'recipient'}. Subject: {subject or 'as appropriate'}. Write ONLY the email body.",
            use_memory=False,
        )
        return draft_email(to=to, subject=subject, body=body), None
    except Exception:
        return "Could not create email draft.", None


def _tool_get_calendar(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    if params.get("query"):
        return _tool_search_calendar(params, query, spoken)
    try:
        from amigo.services.calendar_integration import get_calendar_summary_text, get_upcoming_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None
        raw_days = params.get("days")
        try:
            days = int(raw_days) if raw_days is not None else 1
        except (ValueError, TypeError):
            days = 1
        return (get_calendar_summary_text() if days <= 1 else get_upcoming_summary_text(days=days)), None
    except Exception:
        return "Could not read calendar.", None


def _tool_search_calendar(params, query, spoken):
    try:
        from amigo.services.calendar_integration import is_outlook_available, search_events
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None
        q = (params or {}).get("query", query).strip()
        results = search_events(q, days=30)
        if results:
            lines = [f"Found {len(results)} event(s) matching '{q}':"]
            for i, e in enumerate(results, 1):
                loc = f" at {e['location']}" if e.get("location") else ""
                lines.append(f"  {i}. {e['subject']}{loc} - {e['start']}")
            return "\n".join(lines), None
        return f"No calendar events found matching '{q}'.", None
    except Exception:
        return "Could not search calendar.", None


def _tool_confirm_action(params, query, spoken):
    global _PENDING_CONFIRMATION
    pending = get_pending_confirmation()
    if not pending:
        return "There's nothing waiting for confirmation right now.", None

    params = params if isinstance(params, dict) else {}
    target_action = str(params.get("action") or params.get("tool") or "").strip().lower()
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

    remaining_actions = pending.get("remaining_actions") or []
    _PENDING_CONFIRMATION = None
    first_res = execute_tool(pending["tool"], {**pending["params"], "confirmed": True}, query=pending["query"], spoken="")

    # Continue any remaining steps in a multi-step command after confirmation
    if remaining_actions:
        rest_results = execute_actions(remaining_actions, query=pending["query"])
        all_spoken = [first_res[0]] + [r[0] for r in rest_results if r[0]]
        last_url = next((r[1] for r in reversed(rest_results) if r[1]), first_res[1])
        merged_meta = {**(first_res[2] or {}), "multi_step_resumed": True}
        return " ".join(s for s in all_spoken if s).strip(), last_url, merged_meta

    return first_res


def _tool_cancel_action(params, query, spoken):
    global _PENDING_CONFIRMATION
    had_pending = get_pending_confirmation() is not None
    _PENDING_CONFIRMATION = None
    return ("Okay, I won't do that." if had_pending else "Nothing to cancel."), None


def _tool_get_clipboard(params, query, spoken):
    from amigo.core.llm_agent import get_clipboard_text
    clip = get_clipboard_text()
    if clip:
        return f"Clipboard content:\n{clip}", None, {"clipboard": clip}
    return "The clipboard is currently empty.", None, {"clipboard": ""}


def _tool_manage_voices(params, query, spoken):
    """Schema-driven TTS voice catalog & selection handler (zero hardcoded keyword checks)."""
    from amigo.utils.tts import get_active_voice, get_voice_catalog, set_voice

    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "list").strip().lower()
    voice_param = str(params.get("voice") or "").strip().lower()
    gender_param = str(params.get("gender") or "").strip().lower()
    catalog = get_voice_catalog()
    active_key = get_active_voice()
    active_info = catalog.get(active_key, {})
    active_name = active_info.get("name", active_key)

    if action != "list" or voice_param or gender_param:
        chosen_key = None
        if gender_param:
            for k, v in catalog.items():
                if v.get("gender", "").lower() == gender_param and k != active_key:
                    chosen_key = k
                    break
            if not chosen_key:
                for k, v in catalog.items():
                    if v.get("gender", "").lower() == gender_param:
                        chosen_key = k
                        break
        elif voice_param:
            for k, v in catalog.items():
                name = v.get("name", "").lower()
                if voice_param == k.lower() or voice_param == name or voice_param in name or voice_param in k.lower():
                    chosen_key = k
                    break

        if chosen_key:
            display_name = set_voice(chosen_key)
            return f"Switched voice to {display_name}.", None, {
                "action": "set",
                "voice": chosen_key,
                "display_name": display_name,
            }
        return f"I couldn't find a voice matching '{voice_param or gender_param}'.", None, {
            "action": "set",
            "error": "voice_not_found",
        }

    total_voices = len(catalog)
    sample_names = [v.get("name", k) for k, v in list(catalog.items())[:5]]
    spoken_summary = (
        f"I have {total_voices} voices available. Currently using {active_name}. "
        f"Popular options include {', '.join(sample_names[:-1])}, and {sample_names[-1]}."
        if len(sample_names) > 1
        else f"I have {total_voices} voices available. Currently using {active_name}."
    )
    meta = {
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
    return spoken_summary, None, meta


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
        "Search, read, extract facts, or answer questions from the user's indexed local documents, PDFs, DOCX, spreadsheets, notes, invoices, resumes, or reports. ALWAYS use instead of chat whenever the user asks about their personal files or documents.",
        {
            "query": _p("string", "Specific question or search terms to look up inside indexed documents"),
            "filepath": _p("string", "Optional document filename or topic hint if user names a specific file"),
            "target_index": _p("integer", "Optional ordinal index from Recent Results List: 0=first, 1=second, -1=last"),
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
        "Search and list matching local file paths on disk by filename or keyword (use when user wants to locate or list files, not answer questions about their contents).",
        {"query": _p("string", "Search keyword or filename")},
        ["query"],
    ),
    _tool(
        "open_file",
        "Open an existing local file in its default desktop viewer (supports concrete path/name or target_index from recent results).",
        {
            "path": _p("string", "Concrete filename or path"),
            "target_index": _p("integer", "Ordinal index: 0=first, 1=second, -1=last"),
        },
    ),
    _tool(
        "chat",
        "General knowledge, explanations, reasoning, creative conversation, greetings, assistant questions, and clarifying questions when required details are missing. NEVER use when the user asks about their local documents/files (use document_qa).",
        {"response": _p("string", "Direct spoken reply")},
    ),
    _tool(
        "web_search",
        "Search the web for live news, sports, real-time facts, research, or online lookups.",
        {"query": _p("string", "Search query")},
        ["query"],
    ),
    _tool(
        "stock_quote",
        "Fetch live price quote for a financial ticker symbol or asset (e.g., MSFT, NVDA, BTC).",
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
        "Control or inspect active media playback (play/resume, pause, next track, previous track, or current track status).",
        {"action": _p("string", "play | pause | next | previous | status")},
        ["action"],
    ),
    _tool(
        "play_youtube",
        "Search YouTube by song title, artist, or video name to start new playback.",
        {"query": _p("string", "Song, video title, or search query")},
        ["query"],
    ),
    _tool(
        "set_volume",
        "Adjust system audio volume (mute, unmute, volume_up, volume_down, or set_volume with numeric level 0-100).",
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
    _tool(
        "take_screenshot",
        "Capture and save a screenshot of the monitor.",
        {},
    ),
    _tool(
        "read_screen",
        "Inspect visible GUI text, active window contents, or visual UI elements via screen OCR/vision.",
        {"question": _p("string", "Question about visible screen contents")},
    ),
    _tool(
        "act_in_app",
        "Perform UI automation inside desktop windows: clicking controls, filling fields, choosing dropdown options, scrolling pages, pressing keys, or executing ordered UI steps.",
        {
            "app": _p("string", "App name or 'front'"),
            "steps": _p("array", "Ordered step objects: {do, text/field/option/key/direction}"),
            "action": _p("string", "Single action: click | fill | type | press | scroll | choose"),
            "target": _p("string", "Control text, field label, or scroll direction (up | down | left | right)"),
            "text": _p("string", "Text to type/fill or key to press"),
        },
        tool_aliases={"screen_act": {}},
    ),
    _tool(
        "read_window",
        "Inspect structured UI controls (buttons, fields, labels, values) and visible text of the front or named application window.",
        {"target": _p("string", "Window target: 'front' or application name")},
    ),
    _tool(
        "open_website",
        "Open a website, URL, or domain in the web browser.",
        {
            "url": _p("string", "Concrete URL or domain"),
            "title": _p("string", "Optional page title"),
        },
        ["url"],
    ),
    _tool(
        "open_app",
        "Launch an installed Windows desktop application by name.",
        {"name": _p("string", "Concrete application name")},
        ["name"],
    ),
    _tool(
        "close_app",
        "Close or terminate a running desktop application or window.",
        {"name": _p("string", "Application name")},
    ),
    _tool(
        "window_mgmt",
        "Manage desktop window layout and focus (minimize_all, maximize, restore, switch_window).",
        {"action": _p("string", "minimize_all | maximize | restore | switch_window")},
        ["action"],
    ),
    _tool(
        "power_control",
        "Control PC power state (lock, sleep, restart, shutdown, cancel_shutdown).",
        {"action": _p("string", "lock | sleep | restart | shutdown | cancel_shutdown")},
        ["action"],
    ),
    _tool(
        "empty_recycle_bin",
        "Empty the Windows Recycle Bin.",
        {},
    ),
    _tool(
        "system_status",
        "Report PC hardware utilization (CPU load, RAM usage, disk space, battery level).",
        {},
    ),
    _tool(
        "open_settings",
        "Open the Windows Settings GUI window (use setting='home' for general settings, or a named section like 'display', 'bluetooth', 'sound', 'network').",
        {"setting": _p("string", "Settings section name or 'home'")},
    ),
    _tool(
        "control_windows_system",
        "Control hardware power profiles and modes (battery saver, balanced, high performance).",
        {"action": _p("string", "Power profile or mode name")},
    ),
    _tool(
        "screen_pen_annotate",
        "Draw visual explanations, comparisons, flowcharts, 2D graphs, bar charts, tables, timelines, LaTeX math derivations, molecules, world maps, quizzes, 3D models, or live on-screen UI highlights/annotations.",
        {
            "action": _p(
                "string",
                "diagram | flow | graph | bars | table | timeline | math | geometry | molecule | map | quiz | model | card | circle | ring | arrow | box | underline | highlight | spotlight | clear",
            ),
            "topic": _p("string", "Concept, regions, comparison subject, or system to visualize"),
            "data": _p("object", "Optional structured diagram payload"),
            "label": _p("string", "Existing on-screen UI element text to locate and mark"),
            "x": _p("number", "Target X coordinate (0-1000 normalized)"),
            "y": _p("number", "Target Y coordinate (0-1000 normalized)"),
            "color": _p("string", "Palette tone (teal, coral, amber, sky, lilac, lime)"),
        },
        tool_aliases={"screen_pen": {}, "screen_annotate": {}},
    ),
    _tool(
        "screen_pen_guide",
        "Draw a step-by-step interactive on-screen walkthrough highlighting UI controls with numbered rings and captions.",
        {
            "steps": _p("array", "Ordered UI steps: [{target: '...', caption: '...'}]"),
            "topic": _p("string", "Title of the walkthrough"),
            "app": _p("string", "Optional target application name"),
        },
    ),
    _tool(
        "change_setting",
        "Change or toggle a named Windows setting (e.g., Wi-Fi, Bluetooth, dark mode, night light).",
        {
            "setting": _p("string", "Concrete setting name"),
            "value": _p("string", "on | off | toggle"),
        },
        ["setting"],
    ),
    _tool(
        "get_setting",
        "Check the current on/off state of a named Windows setting.",
        {"setting": _p("string", "Concrete setting name")},
        ["setting"],
    ),
    _tool(
        "open_history",
        "Open or close the assistant conversation history panel.",
        {"action": _p("string", "open | close")},
    ),
    _tool(
        "get_weather",
        "Fetch current weather or forecast for a city or local area.",
        {"city": _p("string", "Target city name (optional if local)")},
    ),
    _tool(
        "set_timer",
        "Start a countdown timer for a specified duration.",
        {
            "duration": _p("string", "Duration in units (e.g. 10m, 45s, 1h 30m)"),
            "seconds": _p("integer", "Total seconds"),
            "label": _p("string", "Optional timer label"),
        },
    ),
    _tool(
        "set_reminder",
        "Schedule a reminder for a future time. Convert relative times to units (e.g. '20m', '2h') or exact dates using Current Time.",
        {
            "message": _p("string", "Reminder message"),
            "time": _p("string", "Relative offset (e.g. 20m, 1h) or ISO/clock time (e.g. 2026-11-15T09:00:00, 17:30)"),
        },
        ["message", "time"],
    ),
    _tool(
        "list_reminders",
        "List active scheduled reminders.",
        {},
    ),
    _tool(
        "cancel_reminder",
        "Cancel a specific scheduled reminder/timer by topic, or all of them if explicitly requested.",
        {
            "query": _p("string", "Specific reminder or timer topic to cancel"),
            "all": _p("boolean", "True ONLY when user explicitly asks to cancel all reminders/timers"),
        },
    ),
    _tool(
        "snooze_reminder",
        "Snooze a triggered or pending reminder for a given duration (e.g. 'snooze 10 minutes', 'snooze for 5 minutes').",
        {
            "duration": _p("string", "Duration to snooze for (e.g. '10 minutes', '5m', '30m')"),
            "duration_seconds": _p("number", "Duration in seconds to snooze"),
            "reminder_id": _p("string", "Optional ID of the reminder to snooze"),
        },
    ),
    _tool(
        "start_focus_mode",
        "Start a dedicated focus session (e.g. 'focus for 30 minutes', 'start focus session') with distraction prevention and gentle nudges.",
        {
            "duration_minutes": _p("integer", "Focus session duration in minutes (default 30)"),
            "goal": _p("string", "Focus goal or task name"),
        },
    ),
    _tool(
        "morning_briefing",
        "Start the day with an overview of weather, today's schedule/reminders, and PC system health.",
        {},
    ),
    _tool(
        "memory_recall",
        "Store ('store') or retrieve ('recall') personal user facts and preferences.",
        {
            "action": _p("string", "store | recall"),
            "fact": _p("string", "Fact to remember when action='store'"),
            "query": _p("string", "Topic to recall when action='recall'"),
        },
        ["action"],
    ),
    _tool(
        "get_calendar",
        "Retrieve upcoming Outlook calendar schedule and events.",
        {"days": _p("integer", "Days ahead (default 1)")},
    ),
    _tool(
        "read_emails",
        "Read, summarize, or search Outlook emails.",
        {
            "action": _p("string", "read | search | unread"),
            "query": _p("string", "Search query or sender"),
            "count": _p("integer", "Max emails to summarize (default 5)"),
        },
    ),
    _tool(
        "unread_emails",
        "Report unread Outlook email count and subjects.",
        {},
    ),
    _tool(
        "draft_email",
        "Compose an Outlook email draft (supports '$prev' in prompt to include prior step output).",
        {
            "to": _p("string", "Recipient"),
            "subject": _p("string", "Subject"),
            "prompt": _p("string", "Email instructions or content"),
        },
        ["prompt"],
    ),
    _tool(
        "generate_content",
        "Draft structured standalone content such as code, letters, essays, or summaries.",
        {
            "type": _p("string", "code | letter | essay | summary"),
            "topic": _p("string", "Topic or specification"),
            "tone": _p("string", "Optional tone"),
            "destination": _p("string", "Optional target app"),
        },
        ["type", "topic"],
    ),
    _tool(
        "regenerate_content",
        "Revise the last generated draft based on feedback.",
        {"feedback": _p("string", "Revision instructions")},
        ["feedback"],
    ),
    _tool(
        "revert_content",
        "Revert the active draft to its previous version.",
        {},
    ),
    _tool(
        "insert_content",
        "Paste the active generated AI draft into a target application.",
        {"destination": _p("string", "Target app name")},
        ["destination"],
    ),
    _tool(
        "type_text",
        "Type text into the active or target window (supports '$prev' to type prior step output).",
        {
            "text": _p("string", "Text to type"),
            "app": _p("string", "Optional target app"),
        },
        ["text"],
    ),
    _tool(
        "press_key",
        "Send a keyboard key or shortcut combination.",
        {"keys": _p("string", "Key or shortcut (e.g., enter, escape, ctrl+s)")},
        ["keys"],
    ),
    _tool(
        "confirm_action",
        "Confirm and execute a pending action previously asked for confirmation by Amigo.",
        {},
    ),
    _tool(
        "cancel_action",
        "Cancel a pending action awaiting confirmation.",
        {},
    ),
    _tool(
        "get_clipboard",
        "Read text currently stored on the Windows clipboard.",
        {},
    ),
    _tool(
        "stop",
        "Stop active assistant speech output.",
        {},
    ),
    _tool(
        "exit",
        "Quit and exit the Amigo assistant application.",
        {},
    ),
    _tool(
        "manage_voices",
        "List available TTS voices or switch the speaking voice by name or gender.",
        {
            "action": _p("string", "list | set"),
            "voice": _p("string", "Voice name or ID when action='set'"),
            "gender": _p("string", "Optional 'male' or 'female' when action='set'"),
        },
        ["action"],
    ),
]

VALID_TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS} | set(UI_TOOL_HANDLERS.keys())
_TOOL_ALIAS_MAP = {
    alias: (td["name"], defaults)
    for td in TOOL_DEFINITIONS
    for alias, defaults in (td.get("tool_aliases") or {}).items()
}
_TOOLS_BLOCK_CACHE: str | None = None


def register_tool(
    name: str,
    handler: Any,
    description: str = "",
    parameters: dict | None = None,
    required: list | None = None,
) -> None:
    """Dynamically registers a new tool at runtime without hardcoding."""
    global _TOOLS_BLOCK_CACHE, _MASTER_PROMPT_CACHE
    entry = _tool(name, description, parameters or {}, required or [])
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


def _tools_block() -> str:
    global _TOOLS_BLOCK_CACHE
    if _TOOLS_BLOCK_CACHE is None:
        rows = []
        for t in TOOL_DEFINITIONS:
            props = (t.get("parameters") or {}).get("properties") or {}
            req = set((t.get("parameters") or {}).get("required") or [])
            sig = ", ".join(f"{k}*" if k in req else k for k in props)
            rows.append(f"- {t['name']}({sig}): {t['description']}")
        _TOOLS_BLOCK_CACHE = "\n".join(rows)
    return _TOOLS_BLOCK_CACHE


_MASTER_SYSTEM_PROMPT = """You are Amigo, an intelligent voice and desktop AI assistant on the user's Windows PC.

CORE OPERATING PRINCIPLES:
1. Intent Understanding: Understand user intent and select the single best tool (or sequence of tools) from TOOLS AVAILABLE below based on the user's underlying goal in any phrasing or language.
2. Single-Step & Multi-Step Execution:
   - If the user asks for one action, output an `actions` array with 1 tool object.
   - If the user asks for multiple sequential or compound actions (e.g., "open X and do Y", "find file X and summarize it", "check weather in Y and set a reminder", "search for X and email it to Z"), decompose the request into an ordered list of tool objects in `actions`.
   - Use `"$prev"` inside a subsequent step's string parameter whenever that step needs the text/result produced by the immediately preceding step.
3. Parameter Grounding:
   - Extract parameter values strictly from the user's message, conversation history, or active runtime context.
   - Never fabricate app names, URLs, setting names, file paths, or numeric levels that were not stated or implied by context.
   - If a required target or value is genuinely missing or ambiguous, use `chat` to ask one brief, natural clarifying question.
4. Tool Selection Guidance:
   - Document Q&A (CRITICAL): ALWAYS use `document_qa` whenever the user asks any question about their indexed documents, files, PDFs, invoices, resumes, reports, or personal records (even if they don't say the word "document"—e.g. "what is my invoice total?", "what does the contract say about termination?", "check my resume for skills"). NEVER answer document questions with `chat`. Use `summarize_document` only when asked for a full-document summary, and `find_file` / `open_file` only to locate or launch a file.
   - Use `act_in_app` for in-window UI interactions (clicking controls, filling fields, scrolling pages, pressing keys).
   - Use `screen_pen_annotate` for visual explanations, comparisons, flowcharts, bar charts, comparison tables, maps, math derivations, 3D models, or marking existing screen elements (`label`).
     * Whenever the user asks to compare, contrast, or show differences/pros and cons between two or more things, use `screen_pen_annotate` with `action="table"` or `action="bars"`.
   - Use `screen_pen_guide` for step-by-step interactive on-screen UI walkthroughs.
   - Use `web_search` for live online information, `get_weather` for weather, `stock_quote` for ticker prices, and `chat` for general knowledge, reasoning, and conversation.
5. Output Format:
   - Output ONLY valid JSON matching this exact schema:
     {"actions": [{"tool": "<tool_name>", "params": {<parameters>}}]}

TOOLS AVAILABLE:
<<TOOLS>>

EXAMPLES:
User: what does my contract say about termination notice?
Assistant: {"actions": [{"tool": "document_qa", "params": {"query": "termination notice", "filepath": "contract"}}]}

User: what is the total amount due on the electricity invoice?
Assistant: {"actions": [{"tool": "document_qa", "params": {"query": "total amount due electricity invoice", "filepath": "invoice"}}]}

User: open notepad and write a short haiku about rain
Assistant: {"actions": [{"tool": "open_app", "params": {"name": "notepad"}}, {"tool": "generate_content", "params": {"type": "essay", "topic": "short haiku about rain"}}, {"tool": "insert_content", "params": {"destination": "notepad"}}]}

User: find my budget file and summarize it
Assistant: {"actions": [{"tool": "find_file", "params": {"query": "budget"}}, {"tool": "summarize_document", "params": {"target_index": 0}}]}

User: search online for latest space telescope discoveries and draft an email to Alex about it
Assistant: {"actions": [{"tool": "web_search", "params": {"query": "latest space telescope discoveries"}}, {"tool": "draft_email", "params": {"to": "Alex", "subject": "Latest Space Telescope Discoveries", "prompt": "$prev"}}]}

User: compare python and rust
Assistant: {"actions": [{"tool": "screen_pen_annotate", "params": {"action": "table", "topic": "Python vs Rust comparison"}}]}

User: show a comparison chart of Python, Rust, and Go popularity
Assistant: {"actions": [{"tool": "screen_pen_annotate", "params": {"action": "bars", "topic": "Python, Rust, and Go popularity"}}]}

User: show a world map highlighting Japan, Germany, and Brazil
Assistant: {"actions": [{"tool": "screen_pen_annotate", "params": {"action": "map", "topic": "Japan, Germany, Brazil"}}]}

User: scroll down on this page
Assistant: {"actions": [{"tool": "act_in_app", "params": {"action": "scroll", "target": "down"}}]}

User: walk me through changing my display scale in Settings
Assistant: {"actions": [{"tool": "screen_pen_guide", "params": {"steps": [{"target": "System", "caption": "Open System settings"}, {"target": "Display", "caption": "Select Display options"}, {"target": "Scale", "caption": "Adjust the scale percentage"}], "topic": "Change Display Scale"}}]}
"""

_MASTER_PROMPT_CACHE: str | None = None


def get_static_master_prompt() -> str:
    global _MASTER_PROMPT_CACHE
    if _MASTER_PROMPT_CACHE is None:
        _MASTER_PROMPT_CACHE = _MASTER_SYSTEM_PROMPT.replace("<<TOOLS>>", _tools_block())
    return _MASTER_PROMPT_CACHE


get_static_router_prefix = get_static_master_prompt


def build_agent_system_prompt(query: str = "", conversation_history: list | None = None) -> str:
    """Backward-compatible helper returning master prompt with active draft/state lines."""
    prefix = get_static_master_prompt()
    state_lines = []
    if pending := _pending_confirmation_line():
        state_lines.append(pending)
    state = rag_engine.get_active_state(clean_expired=True) or {}
    if gen_info := state.get("generated_content"):
        if isinstance(gen_info, dict) and gen_info.get("content"):
            state_lines.append(f"Active Generated Draft: {gen_info.get('type', 'content')} about '{gen_info.get('topic', '')}'")
    if active_ctx := rag_engine.get_active_context_prompt():
        state_lines.append(active_ctx)
    if state_lines:
        return prefix + "\n\nACTIVE STATE:\n" + "\n".join(state_lines)
    return prefix


def _build_router_messages(query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Build multi-turn chat messages for the router:
    - Message 0 (`system`): 100% static unified schema for instant KV cache reuse.
    - Messages 1..N (`user`/`assistant`): Last 3 turns formatted in JSON action form.
    - Final Message (`user`): Runtime context (time, active state, profile facts) + query.
    """
    messages: list[dict] = [{"role": "system", "content": get_static_master_prompt()}]
    messages.extend(build_unified_history_messages(conversation_history=conversation_history, max_turns=3, for_router=True))

    pending = _pending_confirmation_line()
    runtime_ctx = build_turn_runtime_context(query, pending_confirmation=pending)
    user_payload = f"{runtime_ctx}\n\nUser Question: {query}\n[Mode: Voice | Output: JSON]" if runtime_ctx else f"{query}\n[Mode: Voice | Output: JSON]"
    messages.append({"role": "user", "content": user_payload})
    return messages


_JSON_DECODER = json.JSONDecoder()
_ROUTER_STOP = ["<|endoftext|>", "<｜end of sentence｜>", "<|im_end|>", "<|im_start|>", "<eos>", "<turn|>", "<|turn|>"]


def _clean_json_text(text: str) -> str:
    from amigo.core.llm_agent import _strip_reasoning
    clean, _ = _strip_reasoning(text)
    clean = re.sub(r"```(?:json)?\s*", "", clean).replace("```", "").strip()
    clean = re.sub(r",\s*([\]}])", r"\1", clean)
    clean = re.sub(r'\{\s*["\']\s*\}', "{}", clean)
    clean = re.sub(r'\{\s*["\']\s*\]', "{}]", clean)
    clean = re.sub(r'\)\s*\]\s*\}?\s*$', "}]}", clean)
    open_b = clean.count("{") - clean.count("}")
    open_k = clean.count("[") - clean.count("]")
    if open_k > 0:
        clean += "]" * open_k
    if open_b > 0:
        clean += "}" * open_b
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


def _coerce_params(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return {k: v for k, v in raw.items() if k != "confirmed"} if isinstance(raw, dict) else {}


def _call_from_obj(obj) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("actions"), list):
        calls = []
        for item in obj["actions"]:
            calls.extend(_call_from_obj(item))
        return calls
    tool_cand = obj.get("tool") or obj.get("action") or obj.get("name")
    if not isinstance(tool_cand, str):
        return []
    t_name = re.sub(r"\([^)]*\)$", "", tool_cand.strip()).strip().lower()
    if t_name not in VALID_TOOL_NAMES:
        return []
    raw_params = obj.get("params") or obj.get("arguments") or obj.get("parameters") or {}
    params = _coerce_params(raw_params)
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

    xml_blocks = re.findall(
        r'<(?:tool_call|invoke|function_call)(?:[^>]*)>([\s\S]*?)</(?:tool_call|invoke|function_call)>',
        response,
        flags=re.I,
    )
    for m in re.finditer(r'<invoke\s+name=["\']([^"\']+)["\']\s*>([\s\S]*?)</invoke>', response, flags=re.I):
        fn_name = m.group(1).strip().lower()
        body = m.group(2)
        params = {}
        for p in re.finditer(r'<parameter\s+name=["\']([^"\']+)["\']\s*>([\s\S]*?)</parameter>', body, flags=re.I):
            params[p.group(1).strip()] = p.group(2).strip()
        if fn_name in VALID_TOOL_NAMES:
            calls.append({"tool": fn_name, "params": params, "speak": ""})

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
            params = {}
            if inline_args:
                for k, v in re.findall(r'([a-zA-Z0-9_]+)\s*=\s*["\']([^"\']*)["\']', inline_args):
                    params[k.strip()] = v.strip()
            for p in re.finditer(r'<parameter=([a-zA-Z0-9_]+)>\s*([\s\S]*?)\s*</parameter>', body, flags=re.I):
                params[p.group(1).strip()] = p.group(2).strip()
            if fn_name in VALID_TOOL_NAMES:
                calls.append({"tool": fn_name, "params": params, "speak": ""})

    clean = _clean_json_text(response)
    for _, _, obj in _iter_json_objects(clean):
        calls.extend(_call_from_obj(obj))

    valid = [c for c in calls if c["tool"] in VALID_TOOL_NAMES]
    deduped: list[dict] = []
    for c in valid:
        if not deduped or not (c["tool"] == deduped[-1]["tool"] and c.get("params") == deduped[-1].get("params")):
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
    if "{" in clean:
        m = re.search(r'"(?:response|speak|reply)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"?', clean)
        if m:
            try:
                val = json.loads(f'"{m.group(1)}"')
            except Exception:
                val = m.group(1)
            return _clean_final_dialogue(val)

    non_tool = re.sub(
        r'<(?:tool_call|invoke|function_call)(?:[^>]*)>[\s\S]*?</(?:tool_call|invoke|function_call)>',
        '',
        clean,
        flags=re.I,
    ).strip()
    non_tool = re.sub(r'```(?:json|xml)?\s*', '', non_tool).replace('```', '').strip()
    from amigo.core.llm_agent import _strip_reasoning
    non_tool, _ = _strip_reasoning(non_tool)

    if not tool_calls and non_tool and not non_tool.startswith("{") and not non_tool.startswith("["):
        return _clean_final_dialogue(non_tool)
    return ""


def _chat_action(text: str = "") -> dict:
    return {"tool": "chat", "params": {"response": text} if text else {}, "speak": text}


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    """Fast path for empty input only; all non-empty queries go through LLM intent understanding."""
    if not query or not query.strip():
        return {"tool": "chat", "params": {"response": ""}, "speak": ""}
    return None


_TOOL_SCHEMA_MAP: dict[str, dict] = {}


def get_tool_schema(tool_name: str) -> dict | None:
    global _TOOL_SCHEMA_MAP
    if not _TOOL_SCHEMA_MAP:
        _TOOL_SCHEMA_MAP = {t["name"]: t for t in TOOL_DEFINITIONS}
    return _TOOL_SCHEMA_MAP.get(tool_name)


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
    parts = [str(query or "").strip()]
    if conversation_history:
        for turn in conversation_history[-3:]:
            if isinstance(turn, dict):
                parts.extend(
                    str(v)
                    for v in (
                        turn.get("user"),
                        turn.get("assistant"),
                        turn.get("assistant_reply"),
                        turn.get("response"),
                        turn.get("text"),
                        turn.get("content"),
                    )
                    if v
                )
            elif turn:
                parts.append(str(turn))
    return " ".join(p for p in parts if p)


def _is_tokens_grounded(value: str, grounding_text: str) -> bool:
    """Unicode-aware token grounding check across any language without hardcoded word lists."""
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
            if vt == gt:
                return True
            if len(gt) >= 4 and (gt in vt or vt in gt):
                return True
            if len(vt) >= 4 and len(gt) >= 4 and difflib.SequenceMatcher(None, vt, gt).ratio() >= 0.78:
                return True
    return False


def _validate_action_plan(tool_calls: list[dict], grounding_text: str | None = None) -> list[str]:
    """Validates single-step and multi-step action plans for schema completeness and grounding."""
    errors = []
    prior_tools: set[str] = set()

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

        # Multi-step exemption: open_file may omit path/target_index if an earlier step searched files
        if t_name == "open_file":
            has_path = bool(call_params.get("path") and str(call_params["path"]).strip())
            has_idx = call_params.get("target_index") is not None
            has_prior_file = "find_file" in prior_tools
            if not has_path and not has_idx and not has_prior_file:
                errors.append("Tool 'open_file' requires either 'path' or 'target_index'.")
            prior_tools.add(t_name)
            continue

        # Multi-step exemption: downstream text/draft steps may rely on prior step output
        has_prior_output = idx > 0 and bool(
            prior_tools & {"web_search", "generate_content", "read_screen", "read_window", "get_clipboard", "document_qa", "summarize_document", "find_file"}
        )

        missing = []
        for req in required:
            val = call_params.get(req)
            if val is None or str(val).strip() == "":
                if has_prior_output and req in ("prompt", "text", "query", "content"):
                    continue
                missing.append(req)
        if missing:
            errors.append(f"Tool '{t_name}' is missing required parameter(s): {', '.join(missing)}.")

        if t_name == "set_volume":
            act = str(call_params.get("action") or "").lower()
            if act in ("set_volume", "set") or (not act and call_params.get("level") is not None):
                lvl = call_params.get("level")
                is_valid_num = False
                try:
                    if lvl is not None and not isinstance(lvl, bool) and 0 <= int(lvl) <= 100:
                        is_valid_num = True
                except (ValueError, TypeError):
                    pass
                if not is_valid_num:
                    errors.append("Tool 'set_volume' with action='set_volume' requires a numeric 'level' (0-100).")
        elif t_name == "set_brightness":
            lvl = call_params.get("level")
            is_valid_num = False
            try:
                if lvl is not None and not isinstance(lvl, bool) and 0 <= int(lvl) <= 100:
                    is_valid_num = True
            except (ValueError, TypeError):
                pass
            if not is_valid_num:
                errors.append("Tool 'set_brightness' requires a numeric 'level' (0-100).")
        elif t_name == "memory_recall":
            act = str(call_params.get("action") or "").lower()
            if act == "store" and not (call_params.get("fact") or call_params.get("content") or call_params.get("query") or has_prior_output):
                errors.append("Tool 'memory_recall' with action='store' requires 'fact'.")
            elif act == "recall" and not (call_params.get("query") or call_params.get("fact")):
                errors.append("Tool 'memory_recall' with action='recall' requires 'query'.")
        elif t_name == "open_website":
            site_url = str(call_params.get("url") or "").strip()
            parsed_url = urllib.parse.urlparse(site_url if "://" in site_url else f"https://{site_url}")
            if not site_url or "." not in (parsed_url.netloc or ""):
                errors.append("Tool 'open_website' requires a valid domain or URL in 'url'.")
        elif t_name == "read_emails":
            act = str(call_params.get("action") or "").lower()
            if act == "search" and not (call_params.get("query") or "").strip():
                errors.append("Tool 'read_emails' with action='search' requires 'query'.")
        elif t_name == "media_control":
            act = str(call_params.get("action") or "").strip().lower()
            if act not in _MEDIA_ACTION_MAP:
                errors.append("Tool 'media_control' requires 'action' in: resume, pause, next, prev, status.")
        elif t_name in ("time_date", "get_datetime"):
            kind = str(call_params.get("action") or call_params.get("kind") or "").strip().lower()
            if kind not in _DATETIME_KIND_MAP:
                errors.append(f"Tool '{t_name}' requires 'action' in: time, date, both.")
        elif t_name == "power_control":
            act = str(call_params.get("action") or "").strip().lower()
            if act not in _POWER_ACTION_MAP:
                errors.append("Tool 'power_control' requires 'action' in: lock, sleep, restart, shutdown, cancel_shutdown.")
        elif t_name in ("screen_act", "act_in_app"):
            raw_steps = call_params.get("steps")
            if isinstance(raw_steps, list) and raw_steps:
                pass
            elif isinstance(raw_steps, str) and raw_steps.strip().startswith("["):
                pass
            else:
                act = str(call_params.get("action") or call_params.get("do") or "").strip().lower()
                tgt = str(call_params.get("target") or call_params.get("field") or "").strip()
                txt = str(call_params.get("text") or call_params.get("option") or "").strip()
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

        # Grounding check for state-changing tools
        if grounding_text:
            if t_name == "open_app":
                app_val = str(call_params.get("name") or "").strip()
                if app_val and not _is_tokens_grounded(app_val, grounding_text):
                    errors.append(f"Tool 'open_app' parameter name='{app_val}' is not grounded in user's request or context.")
            elif t_name in ("change_setting", "get_setting"):
                set_val = str(call_params.get("setting") or "").strip()
                if set_val and not _is_tokens_grounded(set_val, grounding_text):
                    errors.append(f"Tool '{t_name}' parameter setting='{set_val}' is not grounded in user's request or context.")
            elif t_name == "control_windows_system":
                cat_val = str(call_params.get("category") or call_params.get("action") or call_params.get("mode") or "").strip()
                if not cat_val or not _is_tokens_grounded(cat_val, grounding_text):
                    errors.append(f"Tool 'control_windows_system' target '{cat_val}' is not grounded in user's request or context.")
            elif t_name == "open_website":
                site_url = str(call_params.get("url") or "").strip()
                parsed_url = urllib.parse.urlparse(site_url if "://" in site_url else f"https://{site_url}")
                host_parts = [p for p in (parsed_url.netloc or "").lower().split(".") if p and p != "www"]
                host_stem = " ".join(host_parts[:-1]) if len(host_parts) > 1 else " ".join(host_parts)
                if not host_stem or not _is_tokens_grounded(host_stem, grounding_text):
                    errors.append(f"Tool 'open_website' target '{site_url}' is not grounded in user's request or context.")
            elif t_name in ("set_volume", "set_brightness"):
                act_v = str(call_params.get("action") or "").strip().lower()
                lvl = call_params.get("level")
                need_lvl = t_name == "set_brightness" or not act_v or act_v in ("set_volume", "set")
                if need_lvl and lvl is not None and not isinstance(lvl, bool):
                    try:
                        lvl_int = int(lvl)
                        if not re.search(rf"\b{lvl_int}\b", grounding_text):
                            errors.append(f"Tool '{t_name}' level='{lvl_int}' is not grounded in user's request or context.")
                    except (ValueError, TypeError):
                        pass
            elif t_name in ("screen_act", "act_in_app"):
                raw_steps = call_params.get("steps")
                if not raw_steps:
                    act_s = str(call_params.get("action") or "").strip().lower()
                    tgt_s = str(call_params.get("target") or call_params.get("field") or "").strip()
                    if tgt_s and not (act_s == "scroll" and tgt_s.lower() in _SCROLL_DIRS):
                        if not _is_tokens_grounded(tgt_s, grounding_text):
                            errors.append(f"Tool '{t_name}' target='{tgt_s}' is not grounded in user's request or context.")

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
            p = call.setdefault("params", {})
            p.pop("confirmed", None)

        if call["tool"] == "get_datetime":
            call["tool"] = "time_date"

        if call["tool"] == "set_volume":
            p = call.setdefault("params", {})
            act_v = str(p.get("action") or "").strip().lower()
            if act_v == "set":
                act_v = "set_volume"
                p["action"] = "set_volume"
            if act_v == "set_volume" or not act_v:
                if "level" in p and p.get("level") is not None:
                    p["action"] = "set_volume"
                    try:
                        p["level"] = int(p["level"])
                    except (ValueError, TypeError):
                        pass
                elif not act_v:
                    p["action"] = "set_volume"
            else:
                p.pop("level", None)

        if call["tool"] == "read_emails":
            act = str((call.get("params") or {}).get("action") or "").lower().strip()
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


def _route_with_llm(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    messages = _build_router_messages(query, conversation_history=conversation_history)
    grounding_text = _build_grounding_text(query, conversation_history=conversation_history)
    try:
        response = query_local_llm(
            messages,
            system_prompt="",
            max_tokens=512,
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

    # Schema Validate-and-Repair Pipeline
    schema_errors = _validate_action_plan(tool_calls, grounding_text=grounding_text)
    if schema_errors and tool_calls:
        logger.info("[Tool Registry] Schema validation failed: %s. Attempting repair re-prompt.", schema_errors)
        repair_msg = (
            "Your previous action plan had validation errors:\n"
            + "\n".join(f"- {e}" for e in schema_errors)
            + f"\nOriginal user query: '{query}'\n"
            "Output a corrected JSON action plan using ONLY values stated by the user or in context, "
            "or use 'chat' to ask a short clarifying question if unspecified."
        )
        repair_messages = list(messages)
        repair_messages.append({"role": "assistant", "content": response})
        repair_messages.append({"role": "user", "content": repair_msg})
        try:
            repair_response = query_local_llm(
                repair_messages,
                system_prompt="",
                max_tokens=512,
                temperature=0.0,
                repeat_penalty=1.0,
                thinking=False,
                sanitize=False,
                stop=_ROUTER_STOP,
                raise_on_error=False,
            )
            repaired_calls = _parse_tool_calls(repair_response)
            if repaired_calls and not _validate_action_plan(repaired_calls, grounding_text=grounding_text):
                logger.info("[Tool Registry] Repair re-prompt succeeded: %s", repaired_calls)
                tool_calls = repaired_calls
                response = repair_response
        except Exception as repair_err:
            logger.warning("[Tool Registry] Repair re-prompt encountered error: %s", repair_err)

    post_repair_errors = _validate_action_plan(tool_calls, grounding_text=grounding_text)
    if post_repair_errors:
        logger.warning("[Tool Registry] Action plan invalid after repair: %s. Asking clarifying question.", post_repair_errors)
        first_bad_tool = tool_calls[0].get("tool", "action") if tool_calls else "action"
        friendly_tool = first_bad_tool.replace("_", " ")
        clarify_msg = ""
        try:
            clarify_resp = query_local_llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are Amigo, a voice assistant. Ask a single short plain-English clarifying question "
                            "(under 12 words). Never mention tool names, code, JSON, 'parameter', or field names."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"User said: '{query}'. The specific target or value needed to fulfill '{friendly_tool}' "
                            "was missing or ungrounded. Ask a brief natural question."
                        ),
                    },
                ],
                system_prompt="",
                max_tokens=48,
                temperature=0.0,
                repeat_penalty=1.0,
                thinking=False,
                sanitize=False,
                stop=_ROUTER_STOP,
                raise_on_error=False,
            )
            cand = (clarify_resp or "").strip().strip('"').strip("'")
            if cand and "?" in cand and not cand.startswith("{") and len(cand) < 120:
                clarify_msg = cand.split("?")[0].strip() + "?"
        except Exception:
            pass
        if not clarify_msg:
            subject = "application" if first_bad_tool == "open_app" else friendly_tool
            clarify_msg = f"Which {subject} details would you like me to use?"
        tool_calls = [{
            "tool": "clarification",
            "params": {"query": query, "message": clarify_msg, "candidate_tools": [first_bad_tool]},
            "speak": clarify_msg,
        }]
        return tool_calls, clarify_msg

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


get_agent_actions_with_response = get_agent_actions


def get_agent_action(query: str, conversation_history: list | None = None) -> list[dict]:
    return get_agent_actions(query, conversation_history)[0]


def get_last_played_song(conversation_history: list | None = None) -> dict | None:
    if conversation_history:
        for turn in reversed(conversation_history):
            if isinstance(turn, dict) and turn.get("tool") == "play_youtube":
                q = str((turn.get("params") or {}).get("query") or turn.get("user") or "").strip()
                if q:
                    return {"title": q, "query": q, "url": ""}
        return None

    active_state = get_active_state(clean_expired=True) or {}
    media = active_state.get("current_media")
    if isinstance(media, dict) and media.get("status") == "playing":
        title = media.get("title") or media.get("query")
        if title and title != "No music playing":
            return {"title": title, "query": media.get("query") or title, "url": media.get("url", "")}
    return None


TOOL_ACTION_PROMPTS = {
    "play_youtube": "play audio or video on YouTube",
    "web_search": "search Google on the web",
    "chat": "explain or tell you about this",
    "open_app": "open an application on your PC",
    "close_app": "close an application",
    "window_mgmt": "manage open windows",
    "current_media": "check what song is currently playing",
    "pause_media": "pause media playback",
    "play_media": "resume media playback",
    "next_track": "skip to the next track",
    "prev_track": "go back to the previous track",
    "set_volume": "adjust the volume",
    "get_time": "check the current time",
    "get_date": "check the current date",
    "get_weather": "check the weather forecast",
    "stock_quote": "check the live stock or market price",
    "system_control": "perform a system control action",
    "take_screenshot": "take a screenshot of your screen",
    "lock_pc": "lock your PC",
    "sleep_pc": "put your PC to sleep",
    "restart_pc": "restart your PC",
    "system_status": "check system hardware status",
    "empty_recycle_bin": "empty the recycle bin",
    "screen_vision": "inspect your screen",
    "memory_recall": "check your saved memory",
    "document_qa": "search your documents",
    "get_calendar": "check your calendar schedule",
    "unread_emails": "check your unread emails",
    "set_timer": "set a timer",
    "set_reminder": "set a reminder",
    "find_file": "find files on your PC",
    "type_text": "type text into an application",
    "press_key": "press a keyboard key or shortcut",
    "click_screen": "click on the screen",
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
            if len(result) == 3:
                return result[0] or "", result[1], result[2] if isinstance(result[2], dict) else {}
            if len(result) == 2:
                return result[0] or "", result[1], {}
            if len(result) >= 1:
                return result[0] or "", None, {}
        return result or "", None, {}
    except Exception as e:
        logger.exception("Error executing tool '%s'", tool)
        return "Sorry, I couldn't complete that.", None, {"error": str(e), "tool": tool}
    finally:
        _call_ctx.value = None


def _inject_prev_output(params: dict, last_tool_output: str) -> dict:
    """Substitute '$prev' / '{{prev}}' placeholders and populate empty downstream content fields."""
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
        # Safety Guard: Model-generated action parameters cannot bypass confirmation
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
        last_tool_output = str(out_text or res_spoken or "").strip()

    return results


def build_action_cards(tool: str, params: dict, result_metadata: dict, url: str | None, response_text: str, prompt: str) -> list[dict]:
    """Action cards are replaced by Roamies floating canvas cards."""
    return []