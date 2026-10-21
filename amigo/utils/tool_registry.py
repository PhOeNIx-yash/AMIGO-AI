"""
Tool Registry & Action Dispatcher for Amigo Voice Assistant.
Features:
- Multi-turn conversational routing with 100% static system prompt prefix (instant KV cache hit in llama.cpp).
- Eliminates redundant vector searches inside router prompt assembly.
- Context-aware follow-up resolution across web_search, document_qa, weather, and chat.
- Consolidated duplicate document/file handlers and debounced Windows media inspection.
- Backward-compatible forwarders and structured UI action card builders.
"""

import collections
import datetime
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
    parse_relative_seconds,
)
from amigo.services.app_opener import execute_file_action, find_files, open_folder, open_windows_app
from amigo.services.content_generator import (
    generate_content as _tool_generate_content,
    insert_content as _tool_insert_content,
    regenerate_content as _tool_regenerate_content,
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


def get_quick_feedback(*args, **kwargs):
    from amigo.core.llm_agent import get_quick_feedback as _impl
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


def get_active_state(clean_expired: bool = True) -> dict:
    return rag_engine.get_active_state(clean_expired=clean_expired)


# ── Pre-compiled Regex Patterns ────────────────────────────────

_RE_URLS = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]*[^\s<>"{}|\\^`\[\].,;:!?]')
_RE_MEDIA_CLEAN_TITLE = re.compile(r"^(?:play|playing)\s*:\s*", re.IGNORECASE)
_RE_WAKE_WORD = re.compile(r"^(?:(?:hey|hi|hello|ok|okay)\s+)?amigo\b[\s,.:;!-]*", re.IGNORECASE)

_media_update_cb = None
_ui_broadcast_cb = None
_call_ctx = threading.local()
_PENDING_CONFIRMATION = None
_PENDING_TTL_SECONDS = 90.0


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
    return get_ai_response(
        query,
        web_context=f"Error: The PC is currently offline. Explain to the user that {what}.",
    )


# ═══════════════════════════════════════════════════════════════
#  Tool Handlers (With Automatic Context & Follow-Up Resolution)
# ═══════════════════════════════════════════════════════════════

def _tool_web_search(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()

    if not is_internet_connected():
        return _offline_reply(query, "you cannot search the web right now because you are offline"), None, {"status": "offline"}

    raw_q = str(params.get("query") or query or "").strip() or query
    resolved_q = rag_engine.resolve_contextual_query(raw_q, conv_history)
    target = clean_search_query(resolved_q.replace("(", "").replace(")", "")) or raw_q
    show_in_browser = bool(params.get("show_in_browser"))

    update_active_state("last_search", {"query": target})
    update_active_state("active_subject", {"name": target, "category": "web_search"})

    search_response = search_web(target, max_results=6, fetch_content=False, fast_mode=True)
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(target)}"
    if show_in_browser:
        searchGoogle(target)

    if search_response.results:
        web_context = format_for_llm(search_response, max_chars=3500)
        response = get_ai_response(
            query,
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
        return response, verified_url, meta

    response = get_ai_response(
        query,
        web_context="The web search returned no results. Say so honestly, and only share what you reliably know.",
        conversation_history=conv_history,
    )
    return response, search_url, {"status": "no_results"}


def _tool_stock_quote(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    target = str(params.get("symbol") or params.get("query") or query or "").strip()
    if not is_internet_connected():
        return _offline_reply(query, "you cannot check market quotes offline"), None, {"status": "offline"}

    quote = get_stock_quote(target)
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
        return format_quote(quote), quote.url, meta

    return f"I couldn't find a live stock quote for '{target}'.", None, {"status": "not_found"}


def _tool_open_website(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    if not is_internet_connected():
        return _offline_reply(query, "websites cannot be opened offline"), None, {"status": "offline"}
    raw_url = str(params.get("url") or "").strip()
    title = str(params.get("title") or "").strip()
    if raw_url:
        if re.match(r"^https?://", raw_url, re.I):
            url = raw_url
        elif "://" in raw_url or raw_url.lower().startswith(("javascript:", "file:", "data:")):
            return "I can only open regular web addresses.", None
        else:
            url = f"https://{raw_url}"
        webbrowser.open(url)
        label = title or url
        update_active_state("active_subject", {"name": label, "category": "website"})
        return f"Opening {label}.", url, {"url": url, "title": label}
    searchGoogle(query)
    return "Searched the web.", None, {"query": query}


def _tool_play_youtube(params, query, spoken):
    if not is_internet_connected():
        return _offline_reply(query, "you cannot stream YouTube videos offline"), None, {"status": "offline"}
    q = (params or {}).get("query", query) or query
    media_info = resolve_youtube_video(q)
    clean_q = media_info.get("query") or q

    if not media_info.get("url"):
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
    live_media = _get_live_media_title_from_windows()
    is_playing = _is_system_audio_playing()

    if live_media:
        raw_title, raw_artist, status = live_media
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", raw_title).strip() if raw_title else ""
        by_artist = f" by {raw_artist}" if raw_artist and raw_artist not in ("YouTube Music", "YouTube", "") and raw_artist.lower() not in clean_title.lower() else ""
        update_active_state("current_media", {"title": clean_title, "artist": raw_artist, "status": status.lower()})
        if status.lower() == "paused":
            return f"'{clean_title}'{by_artist} is paused.", None
        return f"Currently playing '{clean_title}'{by_artist}.", None

    if is_playing:
        state = get_active_state(clean_expired=True) or {}
        media_state = state.get("current_media") or {}
        if media_state.get("status") == "playing" and not media_state.get("stale"):
            title = media_state.get("title") or media_state.get("query") or ""
            artist = media_state.get("artist") or ""
            clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", title).strip()
            by_artist = f" by {artist}" if artist and artist not in ("YouTube Music", "YouTube", "") and artist.lower() not in clean_title.lower() else ""
            if clean_title:
                return f"Currently playing '{clean_title}'{by_artist}.", media_state.get("url")
        return "Audio is currently playing on your PC.", None

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
    now = datetime.datetime.now()
    return f"It is {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B')} {now.day}, {now.year}.", None


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
    if not is_internet_connected():
        return _offline_reply(query, "live weather reports cannot be fetched offline"), None, {"status": "offline"}

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
    else:
        result = weather_command(city if city else query) or spoken
    return result, None


def is_app_already_running(app_name: str) -> bool:
    target = (app_name or "").lower().strip()
    if not target or target in ("app", "window", "folder", "file"):
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
        app_name = query.strip()
    if not app_name:
        return spoken or "Which application would you like to open?", None

    clean_target = app_name.strip()
    if open_windows_app(clean_target):
        update_active_state("active_app", {"name": clean_target})
        return spoken or f"Opened {clean_target.title()}.", None

    if is_app_already_running(clean_target):
        update_active_state("active_app", {"name": clean_target})
        return f"{clean_target.title()} is already open.", None

    return f"I couldn't find an app called '{clean_target.title()}' installed on your PC.", None, {"status": "completed", "query": clean_target}


def _tool_open_folder(params, query, spoken):
    folder_name = (params or {}).get("name", "downloads")
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
        lines = [f"I found {len(file_results)} matching file{'s' if len(file_results) != 1 else ''}:"]
        for i, f in enumerate(file_results[:5], 1):
            clean_t = os.path.splitext(f["name"])[0].replace("_", " ").replace("-", " ").title()
            lines.append(f"{i}. {clean_t} ({f['extension']})")
        lines.append("Say 'Open number 1' or ask a question about it.")
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
        return res.get("reply", "I inspected your screen."), None, {
            "window_title": res.get("window_title", "Active Window"),
            "screenshot": res.get("screenshot_path", "amigo_screenshot.png"),
            "text_snippet": screen_text[:300],
            "screen_text": screen_text,
        }
    except Exception:
        return "Could not inspect the screen right now.", None


def _tool_memory_recall(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()
    q = str(params.get("query") or query or "").strip()
    q_low = q.lower()

    fact = str(params.get("fact") or "").strip()
    if fact:
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
    """Unified RAG Q&A handler with full follow-up context and active_file awareness."""
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history(conversation_history)
    filepath = str(params.get("filepath") or "").strip()
    question = str(params.get("question") or params.get("query") or query or "").strip() or query

    resolved_question = rag_engine.resolve_contextual_query(question, conv_history)
    target_path = None
    if filepath:
        target_path = resolve_document_path(filepath)
    elif re.search(r"\b[\w\-.]+\.(?:pdf|docx?|txt|xlsx?|csv|json|py|pptx?|html)\b", question, re.I):
        target_path = resolve_document_path(question)
    elif act_f := (get_active_state(clean_expired=False) or {}).get("active_file"):
        if isinstance(act_f, dict) and act_f.get("path") and os.path.exists(act_f["path"]):
            target_path = act_f["path"]

    ctx = ""
    if target_path and os.path.exists(target_path):
        update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        ctx = rag_engine.build_file_context(target_path, resolved_question)

    if not ctx or len(ctx.strip()) < 60:
        broader_ctx = rag_engine.build_rag_context(resolved_question, top_k=5, conversation_history=conv_history, for_voice=False)
        if broader_ctx:
            ctx = broader_ctx
            if not target_path:
                act_f = (get_active_state(clean_expired=False) or {}).get("active_file")
                if isinstance(act_f, dict) and act_f.get("path") and os.path.exists(act_f["path"]):
                    target_path = act_f["path"]

    if ctx:
        meta = {"matched_docs": [target_path]} if target_path else {"doc_context_used": True}
        response = get_ai_response(question, doc_context=ctx, conversation_history=conv_history)
        return response, None, meta

    return "I couldn't find anything relevant in your indexed documents.", None


def _tool_summarize_document(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    conv_history = _get_conv_history()
    filepath = str(params.get("filepath") or "").strip()
    target_path = None
    if filepath:
        target_path = resolve_document_path(filepath)
    elif re.search(r"\b[\w\-.]+\.(?:pdf|docx?|txt|xlsx?|csv|json|py|pptx?|html)\b", query, re.I):
        target_path = resolve_document_path(query)
    elif act_f := (get_active_state(clean_expired=False) or {}).get("active_file"):
        if isinstance(act_f, dict) and act_f.get("path") and os.path.exists(act_f["path"]):
            target_path = act_f["path"]

    if target_path and os.path.exists(target_path):
        update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        ctx = rag_engine.get_file_summary_context(target_path)
    else:
        ctx = rag_engine.build_rag_context(query, top_k=6, conversation_history=conv_history)

    if ctx:
        target_prompt = query.strip() or "Summarize this document concisely."
        return get_ai_response(target_prompt, doc_context=ctx, conversation_history=conv_history), None
    return "I couldn't find that document to summarize.", None


def _tool_search_knowledge(params, query, spoken):
    conv_history = _get_conv_history()
    q = (params or {}).get("query", query).strip()
    ctx = rag_engine.build_rag_context(q, top_k=5, conversation_history=conv_history)
    return get_ai_response(q, doc_context=ctx, conversation_history=conv_history), None


def _tool_chat(params, query, spoken, conversation_history=None):
    """Conversational reply with full multi-turn memory and automatic RAG context."""
    params = params if isinstance(params, dict) else {}
    doc_context = str(params.get("context") or params.get("doc_context") or "").strip()
    doc_used = bool(doc_context)
    direct_reply = (spoken or params.get("reply") or params.get("response") or "").strip()

    # Fast path: If the router already produced a quality conversational response
    # and no attached document requires re-reading, return immediately (avoids slow 2nd LLM round-trip)
    if direct_reply and not doc_context and len(direct_reply) >= 3:
        return direct_reply, None, ({"doc_context_used": False})

    if not query or not query.strip():
        if direct_reply:
            return direct_reply, None

    conv_history = _get_conv_history(conversation_history)

    if not doc_context and query and not rag_engine._is_conversational_filler(query):
        active_state = get_active_state(clean_expired=True) or {}
        has_active_file = bool(active_state.get("active_file", {}).get("path"))
        if has_active_file:
            try:
                rag_ctx = rag_engine.build_rag_context(
                    query,
                    top_k=4,
                    conversation_history=conv_history,
                    for_voice=True,
                    min_score=0.72,
                )
                if rag_ctx:
                    doc_context = rag_ctx
                    doc_used = True
            except Exception as e:
                logger.debug("[Chat RAG Lookup]: %s", e)

    ai_reply = get_ai_response(query, doc_context=doc_context, conversation_history=conv_history)
    return ai_reply, None, ({"doc_context_used": True} if doc_used else {})


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


def get_pending_confirmation():
    global _PENDING_CONFIRMATION
    if _PENDING_CONFIRMATION and (time.time() - _PENDING_CONFIRMATION["created"]) > _PENDING_TTL_SECONDS:
        _PENDING_CONFIRMATION = None
    return _PENDING_CONFIRMATION


def _pending_confirmation_line() -> str:
    pending = get_pending_confirmation()
    if not pending:
        return ""
    action_name = pending.get("action") or pending.get("tool") or "action"
    return f"[Awaiting user confirmation: user was asked to confirm '{action_name}']"


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
    confirm_result = _require_confirmation("Close app", params, query, spoken or "Closing app")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    name = (params or {}).get("app_name") or (params or {}).get("name") or (params or {}).get("app") or ""
    closed = os_automation.close_app(name)
    if not name or name.lower() in ("current", "active", "this", "window", "app", "application", "it"):
        return "Window closed.", None
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
    return f"I'm not completely sure what you'd like to do with '{q}'. Please choose an option below or clarify.", None, {
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
    if action in ("lock", "lock_pc"):
        return _tool_lock_pc(params, query, "Locking PC")
    if action in ("sleep", "sleep_pc"):
        return _tool_sleep_pc(params, query, "Putting system to sleep")
    if action in ("restart", "restart_pc"):
        return _tool_restart_pc(params, query, "Restarting PC in 30 seconds")
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

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_REFERENT_WORDS = frozenset({"that", "it", "this", "those", "them", "these", "one", "ones", "selected", "same", "number", "no"})


def _ordinal_index(text: str, count: int):
    low = text.lower()
    if re.search(r"\blast\s+one\b", low):
        return count - 1
    m = re.search(r"\b(?:number|no\.?|#)\s*(\d+)\b", low) or re.search(r"\b(\d+)(?:st|nd|rd|th)\b", low)
    if m:
        n = int(m.group(1))
        return n - 1 if 1 <= n <= count else -1
    for word, n in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            return n - 1 if n <= count else -1
    return None


def resolve_document_path(query_or_target: str) -> str | None:
    state = get_active_state(clean_expired=False) or {}
    active_f = state.get("active_file")
    active_path = active_f.get("path") if isinstance(active_f, dict) else None
    active_ok = bool(active_path and os.path.exists(active_path))

    if not query_or_target or not isinstance(query_or_target, str):
        return active_path if active_ok else None

    raw = query_or_target.strip()
    if os.path.isfile(raw):
        return raw

    clean = raw

    found_list = state.get("found_files", [])
    if isinstance(found_list, dict):
        found_list = found_list.get("items", [])
    if isinstance(found_list, list) and found_list:
        idx = _ordinal_index(raw, len(found_list))
        if idx == -1:
            return None
        if idx is not None and os.path.exists(found_list[idx]):
            return found_list[idx]

    tokens = re.findall(r"[a-z0-9']+", clean.lower())
    if not tokens or all(t in _REFERENT_WORDS or t in _ORDINAL_WORDS or t.isdigit() for t in tokens):
        return active_path if active_ok else None

    search_terms = []
    if m_ext := re.search(r"\b([\w\-.]+\.(?:pdf|docx?|txt|xlsx?|csv|json|py|pptx?|html))\b", raw, re.I):
        search_terms.append(m_ext.group(1))
    if m_doc := re.search(r"\b(?:file|document|doc|ticket|sheet)\s+([a-zA-Z0-9_\-]{2,25})", clean, re.I):
        cand = m_doc.group(1).strip()
        if cand and cand.lower() not in _REFERENT_WORDS and cand.lower() not in _ORDINAL_WORDS:
            search_terms.append(cand)

    for term in search_terms:
        if len(term) >= 2:
            matches, _ = find_files(term)
            if matches:
                update_active_state("active_file", {"path": matches[0], "name": os.path.basename(matches[0])})
                return matches[0]

    if search_terms:
        for term in search_terms:
            if term and len(term) >= 3 and term.lower() not in _REFERENT_WORDS:
                results = rag_engine.search_files_by_context(term, top_k=1)
                if results and results[0].get("score", 0) > 0.35:
                    fpath = results[0].get("filepath")
                    if fpath and os.path.exists(fpath):
                        update_active_state("active_file", {"path": fpath, "name": os.path.basename(fpath)})
                        return fpath

    return active_path if active_ok else None


def _tool_file_action(params, query, spoken, default_action="open"):
    params = params if isinstance(params, dict) else {}
    target = params.get("path") or params.get("name") or query
    action = params.get("action") or default_action
    target_path = resolve_document_path(str(target))

    if not (target_path and os.path.exists(target_path)):
        return "I couldn't find that file on this computer.", None

    update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
    ok, msg = execute_file_action(target_path, action)
    if not ok:
        return msg or f"I couldn't {str(action).replace('_', ' ')} that file.", None
    clean_name = os.path.splitext(os.path.basename(target_path))[0].replace("_", " ").replace("-", " ").title()
    return (spoken or f"Opened {clean_name}." if action == "open" else msg or f"Done with {clean_name}."), None


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
        return "Audio muted.", None
    if action == "unmute":
        unmute_fn = getattr(os_automation, "unmute", None)
        if callable(unmute_fn):
            unmute_fn()
        else:
            os_automation.mute()
        return "Audio unmuted.", None
    if action == "volume_up":
        os_automation.volume_up()
        return "Volume increased.", None
    if action == "volume_down":
        os_automation.volume_down()
        return "Volume decreased.", None
    if level in (None, ""):
        return "What volume level would you like, from 0 to 100?", None
    return _level_result(os_automation.set_volume(level), f"Volume set to {level} percent."), None


def _tool_set_brightness(params, query, spoken):
    level = (params or {}).get("level")
    if level in (None, ""):
        return "What brightness level would you like, from 0 to 100?", None
    return _level_result(os_automation.set_brightness(level), f"Brightness set to {level} percent."), None


def _tool_open_settings(params, query, spoken):
    s = str((params or {}).get("setting", "")).strip().lower()
    if _ui_broadcast_cb:
        try:
            if s in ("close", "hide", "dismiss"):
                _ui_broadcast_cb("close_settings", {})
                return "Closed settings.", None
            _ui_broadcast_cb("open_settings", {"setting": s})
        except Exception:
            pass
    if s and s not in ("settings", "setting", "setting tab", "settings tab", "tab", "app settings", "ui", "close"):
        return open_setting(s), None
    return "Opened settings.", None


def _tool_open_history(params, query, spoken):
    """Broadcast open/close history drawer events to the React UI."""
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action", "open")).strip().lower()
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
    return handle_set_timer(params, query), None


def _tool_set_reminder(params, query, spoken):
    msg = handle_set_reminder(params, query)
    reminders = get_active_data().get("reminders", [])
    cards = [{
        "id": f"reminder-{int(time.time())}",
        "title": "Scheduled Reminders",
        "type": "reminder",
        "badge": "reminder",
        "description": msg,
        "selected": True,
        "actionType": "custom",
        "payload": {"tool": "reminder", "reminders": reminders, "message": msg},
    }] if reminders else []
    return msg, None, {"cards": cards, "reminders": reminders}


def _tool_list_reminders(params, query, spoken):
    msg = handle_list_reminders()
    reminders = get_active_data().get("reminders", [])
    cards = [{
        "id": f"reminders-list-{int(time.time())}",
        "title": "My Active Reminders",
        "type": "reminder",
        "badge": "reminder",
        "description": f"{len(reminders)} active reminder(s)",
        "selected": True,
        "actionType": "custom",
        "payload": {"tool": "reminder", "reminders": reminders},
    }] if reminders else []
    return msg, None, {"cards": cards, "reminders": reminders}


def _tool_cancel_reminder(params, query, spoken):
    return handle_cancel_reminder(params), None


def _tool_exit(params, query, spoken):
    return "Goodbye!", None


# ── Email & Calendar Handlers ──────────────────────────────────

def _tool_read_emails(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "").strip().lower()
    if action == "unread" or (not action and "unread" in query.lower()):
        return _tool_unread_emails(params, query, spoken)
    if action == "search" or (not action and params.get("query")):
        return _tool_search_emails(params, query, spoken)
    try:
        from amigo.services.mail_integration import get_email_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available.", None
        summary = get_email_summary_text(int(params.get("count", 5)))
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
        to, subject, prompt = params.get("to", "").strip(), params.get("subject", "").strip(), params.get("prompt", query).strip()
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
        days = int(params.get("days", 1))
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
    _PENDING_CONFIRMATION = None
    return execute_tool(pending["tool"], {**pending["params"], "confirmed": True}, query=pending["query"], spoken="")


def _tool_cancel_action(params, query, spoken):
    global _PENDING_CONFIRMATION
    had_pending = get_pending_confirmation() is not None
    _PENDING_CONFIRMATION = None
    return ("Okay, I won't do that." if had_pending else "Nothing to cancel."), None


UI_TOOL_HANDLERS = {
    "web_search":         _tool_web_search,
    "open_website":       _tool_open_website,
    "show_images":        _tool_show_images,
    "play_youtube":       _tool_play_youtube,
    "get_time":           _tool_get_time,
    "get_date":           _tool_get_date,
    "time_date":          _tool_time_date,
    "get_weather":        _tool_get_weather,
    "open_app":           _tool_open_app,
    "take_screenshot":    _tool_take_screenshot,
    "read_screen":        _tool_read_screen,
    "ask_about_screen":   _tool_read_screen,
    "screen_vision":      _tool_read_screen,
    "type_text":          _tool_type_text,
    "search_and_type":    _tool_search_and_type,
    "press_key":          _tool_press_key,
    "click_screen":       _tool_click_screen,
    "new_tab":            _tool_new_tab,
    "close_app":          _tool_close_app,
    "window_management":  _tool_window_management,
    "window_mgmt":        _tool_window_management,
    "calculate":          _tool_calculate,
    "set_volume":         _tool_set_volume,
    "set_brightness":     _tool_set_brightness,
    "open_settings":      _tool_open_settings,
    "open_history":       _tool_open_history,
    "system_status":      _tool_system_status,
    "hardware_metrics":   _tool_system_status,
    "system_control":     _tool_system_control,
    "restart_pc":         _tool_restart_pc,
    "pause_media":        _tool_pause_media,
    "play_media":         _tool_play_media,
    "resume_media":       _tool_play_media,
    "resume_playback":    _tool_play_media,
    "stock_quote":        _tool_stock_quote,
    "get_stock_quote":    _tool_stock_quote,
    "market_quote":       _tool_stock_quote,
    "stock_price":        _tool_stock_quote,
    "stop":               _tool_stop,
    "stop_speaking":      _tool_stop_speaking,
    "next_track":         _tool_next_track,
    "prev_track":         _tool_prev_track,
    "set_timer":          _tool_set_timer,
    "stopwatch":          _tool_set_timer,
    "set_reminder":       _tool_set_reminder,
    "list_reminders":     _tool_list_reminders,
    "cancel_reminder":    _tool_cancel_reminder,
    "open_folder":        _tool_open_folder,
    "find_file":          _tool_find_file,
    "find_document":      _tool_find_file,
    "open_file":          _tool_file_action,
    "reveal_file":        _make_file_handler("reveal"),
    "copy_file_path":     _make_file_handler("copy_path"),
    "memory_recall":      _tool_memory_recall,
    "current_media":      _tool_get_current_media,
    "get_current_media":  _tool_get_current_media,
    "clarification":      _tool_clarification,
    "exit":               _tool_exit,
    "chat":               _tool_chat,
    "generate_content":   _tool_generate_content,
    "regenerate_content": _tool_regenerate_content,
    "insert_content":     _tool_insert_content,
    "empty_recycle_bin":  _tool_empty_recycle_bin,
    "lock_pc":            _tool_lock_pc,
    "sleep_pc":           _tool_sleep_pc,
    "blocked":            _tool_blocked,
    "ask_document":       _tool_ask_document,
    "document_qa":        _tool_ask_document,
    "summarize_document": _tool_summarize_document,
    "search_knowledge":   _tool_search_knowledge,
    "read_emails":        _tool_read_emails,
    "search_emails":      _tool_search_emails,
    "unread_emails":      _tool_unread_emails,
    "draft_email":        _tool_draft_email,
    "get_calendar":       _tool_get_calendar,
    "search_calendar":    _tool_search_calendar,
    "confirm_action":     _tool_confirm_action,
    "cancel_action":      _tool_cancel_action,
}

for _key, (_func, _msg) in _SIMPLE_OS_ACTIONS.items():
    UI_TOOL_HANDLERS.setdefault(_key, _make_simple_handler(_func, _msg))


# ═══════════════════════════════════════════════════════════════
#  Tool Definitions & Multi-Turn Context-Aware Router
# ═══════════════════════════════════════════════════════════════

def _p(type_: str, description: str = "") -> dict:
    return {"type": type_, "description": description}


def _tool(name: str, description: str, props: dict | None = None, required: list | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []},
    }


TOOL_DEFINITIONS = [
    _tool("chat", "Conversational response: greetings, casual talk, advice, quick math, current date/time, general knowledge, or small talk.",
          {"response": _p("string", "Your direct, natural spoken or written reply to the user (1 to 2 concise sentences)")},
          ["response"]),
    _tool("web_search", "Search the web for real-time facts, news, people, movies, music trivia, release dates, or external knowledge.",
          {"query": _p("string", "Self-contained search query with pronouns resolved from conversation history"),
           "show_in_browser": _p("boolean", "True if user asked to open search results in a browser")}, ["query"]),
    _tool("stock_quote", "Look up real-time stock or crypto prices.", {"symbol": _p("string", "Ticker or asset name")}, ["symbol"]),
    _tool("play_youtube", "Play songs, music, or videos on YouTube.",
          {"query": _p("string", "Exact song, video title, or music search query as spoken by the user. Do NOT invent, assume, or add artist names if the user did not say them.")},
          ["query"]),
    _tool("resume_media", "Resume paused audio/video playback."),
    _tool("pause_media", "Pause currently playing audio/video."),
    _tool("next_track", "Skip to next track."),
    _tool("prev_track", "Go to previous track."),
    _tool("current_media", "Check which song or video is currently playing."),
    _tool("set_volume", "Control system audio volume (mute, unmute, volume_up, volume_down, set_volume).",
          {"action": _p("string", "mute | unmute | volume_up | volume_down | set_volume"), "level": _p("integer", "0-100")}),
    _tool("set_brightness", "Adjust screen brightness.", {"level": _p("integer", "0-100")}, ["level"]),
    _tool("take_screenshot", "Capture a screenshot of the screen."),
    _tool("read_screen", "Inspect current screen content and answer questions about it.", {"question": _p("string", "Question about screen")}),
    _tool("open_app", "Launch an installed desktop app.", {"name": _p("string", "App name")}, ["name"]),
    _tool("close_app", "Close an application or active window.", {"name": _p("string", "App name")}),
    _tool("window_mgmt", "Manage windows (minimize_all, maximize, restore, switch_window, close_window).",
          {"action": _p("string", "Action"), "name": _p("string", "Window title")}, ["action"]),
    _tool("system_control", "System power/control (lock, sleep, restart, shutdown, cancel_shutdown, empty_bin).",
          {"action": _p("string", "Action")}, ["action"]),
    _tool("lock_pc", "Lock the Windows PC immediately."),
    _tool("sleep_pc", "Put the PC into sleep mode."),
    _tool("restart_pc", "Restart the Windows computer."),
    _tool("empty_recycle_bin", "Empty the Windows recycle bin."),
    _tool("system_status", "Check battery, CPU, RAM, and disk status."),
    _tool("open_settings", "Open a Windows or assistant settings page.", {"setting": _p("string", "Setting name")}, ["setting"]),
    _tool("open_history", "Open or view conversational memory and interaction history."),
    _tool("get_weather", "Get weather forecast for a city.", {"city": _p("string", "City name resolved from context")}),
    _tool("set_timer", "Start a countdown timer.", {"duration": _p("integer", "Seconds"), "label": _p("string", "Label")}, ["duration"]),
    _tool("set_reminder", "Create a task reminder.", {"query": _p("string", "Reminder text and time")}, ["query"]),
    _tool("list_reminders", "List pending reminders."),
    _tool("cancel_reminder", "Cancel one or all reminders.", {"query": _p("string", "'all' or reminder title")}, ["query"]),
    _tool("find_file", "Find local files or folders by name or topic.", {"query": _p("string", "Search pattern")}, ["query"]),
    _tool("open_file", "Open a local file by path, name, or ordinal ('number 1', 'it').", {"path": _p("string", "File reference")}, ["path"]),
    _tool("document_qa", "Answer questions or summarize indexed local documents, files, bills, tickets, or personal records.",
          {"query": _p("string", "Self-contained question about document contents"), "filepath": _p("string", "Optional filename")}, ["query"]),
    _tool("memory_recall", "Save a new personal fact ('remember that...') or recall saved user facts/preferences.",
          {"query": _p("string", "Question about saved memory"), "fact": _p("string", "Fact to save if user asked to remember")}),
    _tool("get_calendar", "View or search calendar schedule.", {"days": _p("integer", "Days ahead"), "query": _p("string", "Search keyword")}),
    _tool("read_emails", "Read recent/unread emails or search Outlook.", {"action": _p("string", "recent | unread | search"), "query": _p("string", "Keyword")}),
    _tool("unread_emails", "Check and read unread emails in Outlook."),
    _tool("draft_email", "Draft an email in Outlook.", {"to": _p("string", "Recipient"), "subject": _p("string", "Subject"), "prompt": _p("string", "Body")}, ["prompt"]),
    _tool("generate_content", "Write/draft content (email, code, letter, essay).",
          {"type": _p("string", "Content type"), "topic": _p("string", "Topic"), "tone": _p("string", "Tone"), "destination": _p("string", "App")}, ["type", "topic"]),
    _tool("regenerate_content", "Modify or rewrite the last generated draft.", {"feedback": _p("string", "Modification instructions")}, ["feedback"]),
    _tool("insert_content", "Insert/paste last generated content into a destination app.", {"destination": _p("string", "Target app")}, ["destination"]),
    _tool("type_text", "Type text into an app.", {"text": _p("string", "Text"), "app": _p("string", "App")}, ["text"]),
    _tool("press_key", "Send a keyboard shortcut.", {"keys": _p("string", "Shortcut e.g. ctrl+c")}, ["keys"]),
    _tool("confirm_action", "Confirm a pending action when user says yes/go ahead."),
    _tool("cancel_action", "Cancel a pending action when user says no/cancel."),
    _tool("open_website", "Open a website, portal, search results, or direct deep-link in the browser (e.g. Amazon shopping, Google Flights, Google Maps, online booking, or any destination URL).",
          {"url": _p("string", "Target URL to open (including search or booking parameters)"),
           "title": _p("string", "Short title or label describing the destination or action")}, ["url"]),
    _tool("stop", "Stop active speech or playback."),
    _tool("exit", "Close Amigo."),
]

VALID_TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS} | set(UI_TOOL_HANDLERS.keys())
_TOOLS_BLOCK_CACHE: str | None = None


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


_MASTER_SYSTEM_PROMPT = """You are Amigo, a friendly, intelligent, and capable personal AI voice and desktop assistant on the user's Windows PC.

CORE RULES:
- State the direct answer or solution immediately in the first sentence.
- Keep responses concise and natural (1 to 3 sentences) unless the user asks for detailed explanations, lists, or code.
- Never use robotic filler phrases like 'Certainly!', 'Sure thing!', 'As an AI', or 'According to search results'.
- Never deflect with multi-choice question menus. Answer directly with what you know.
- Only reference 'available documents' or 'in your files' if the user specifically asked about their personal files.
- Resolve pronouns ('it', 'that', 'they', 'he', 'she', 'there') and follow-up questions naturally from conversation history and active environment state.

TOOLS AVAILABLE:
<<TOOLS>>

ROUTING RULES:
1. FOLLOW-UP & MEDIA CONTEXT: Always inspect prior turns. For 'play_youtube', pass the exact song or video query the user said in params['query']. Never guess or invent artist names that the user did not say (e.g. if the user says 'play emptiness', params['query'] MUST be 'emptiness', NOT 'emptiness by <artist>'). If the user asks to 'play it again' or replay music, call 'play_youtube' with the song/artist query from the prior turn. For resuming paused media, call 'resume_media'. For next song, call 'next_track'.
2. DIALOGUE, MATH & GENERAL KNOWLEDGE ('chat'): Use for greetings, casual talk, math, or general conceptual explanations. Put your direct, concise spoken reply in params['response']. If the user specifically asks for the current time or date, call 'get_time' or 'get_date'.
3. VOLUME CONTROL ('set_volume'): When muting, unmuting, or setting volume, pass params['action'] as 'mute', 'unmute', 'volume_up', 'volume_down', or 'set_volume' (with level).
4. BROWSING & DIRECT ACTIONS ('open_website'): Use when user wants to visit a website, portal, or homepage (e.g., 'open booking.com', 'go to reddit', 'open github'). Only construct query URLs for standard public services (Google Flights, Google Maps). Never hallucinate or guess complex internal query parameters or path slugs for shopping/booking sites.
5. WEB SEARCH ('web_search'): Use when user wants to search, find, or look up products, hotels, flights, deals, or items on ANY website or store (e.g., 'find hotels in Goa on Booking.com', 'find running shoes on Flipkart', 'curtain lights on Amazon'). Write the complete query in params['query']. Set show_in_browser=true if user wants to open the results.
6. LOCAL DOCUMENTS & PERSONAL FILES ('document_qa'): Use when asking about the user's saved files, bills, tickets, notes, or follow-ups about an Active File.
7. CONFIRMATIONS: If '[Awaiting user confirmation]' is present and user agrees ('yes', 'sure', 'do it'), call 'confirm_action'. If user declines ('no', 'cancel'), call 'cancel_action'.

When outputting tool actions or direct chat responses, output ONLY valid JSON:
{"actions": [{"tool": "<tool_name>", "params": {<parameters>}}]}"""

_MASTER_PROMPT_CACHE: str | None = None


def get_static_master_prompt() -> str:
    global _MASTER_PROMPT_CACHE
    if _MASTER_PROMPT_CACHE is None:
        _MASTER_PROMPT_CACHE = _MASTER_SYSTEM_PROMPT.replace("<<TOOLS>>", _tools_block())
    return _MASTER_PROMPT_CACHE


get_static_router_prefix = get_static_master_prompt

try:
    from amigo.core.llm_agent import set_master_prompt_provider
    set_master_prompt_provider(get_static_master_prompt)
except Exception:
    pass


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
    - Message 0 (`system`): 100% static unified schema shared with synthesis for 0ms prefix KV cache reuse.
    - Messages 1..N (`user`/`assistant`): Last 3 turns formatted identically to Pass 2.
    - Final Message (`user`): Complete runtime context (time, state, profile facts) + query + [Mode: Voice | Output: JSON].
    """
    messages: list[dict] = [{"role": "system", "content": get_static_master_prompt()}]
    messages.extend(build_unified_history_messages(conversation_history=conversation_history, max_turns=3))

    pending = _pending_confirmation_line()
    runtime_ctx = build_turn_runtime_context(query, pending_confirmation=pending)
    user_payload = f"{runtime_ctx}\n\nUser Question: {query}\n[Mode: Voice | Output: JSON]" if runtime_ctx else f"{query}\n[Mode: Voice | Output: JSON]"
    messages.append({"role": "user", "content": user_payload})
    return messages


_JSON_DECODER = json.JSONDecoder()
_ROUTER_STOP = ["<|im_end|>", "<|endoftext|>", "<|im_start|>"]


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
    tool_cand = obj.get("tool") or obj.get("action") or (obj.get("name") if obj.get("name") in VALID_TOOL_NAMES else None)
    if not isinstance(tool_cand, str) or tool_cand.strip().lower() not in VALID_TOOL_NAMES:
        return []
    t_name = tool_cand.strip().lower()
    params = _coerce_params(obj.get("params") if "params" in obj else obj.get("arguments"))
    speak = obj.get("speak") or (params.get("reply") or params.get("response") if t_name == "chat" else "")
    if t_name == "chat":
        reply_val = params.get("reply") or params.get("response") or speak
        if reply_val:
            params["response"] = reply_val
            params["reply"] = reply_val
            speak = reply_val
    return [{"tool": t_name, "params": params, "speak": speak if isinstance(speak, str) else ""}]


def _parse_tool_calls(response: str) -> list[dict]:
    clean = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.I).strip()
    clean = re.sub(r"<think>[\s\S]*$", "", clean, flags=re.I).strip()
    clean = re.sub(r"```(?:json)?\s*", "", clean).replace("```", "").strip()
    clean = re.sub(r",\s*([\]}])", r"\1", clean)
    calls: list[dict] = []
    for _, _, obj in _iter_json_objects(clean):
        calls.extend(_call_from_obj(obj))
    return [c for c in calls if c["tool"] in VALID_TOOL_NAMES]


def _extract_final_response(response: str, tool_calls: list) -> str:
    clean = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.I).strip()
    clean = re.sub(r",\s*([\]}])", r"\1", clean).replace("```json", "").replace("```", "").strip()
    if clean.startswith("{") or clean.startswith("["):
        m = re.search(r'"(?:response|speak|reply)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"?', clean)
        if m:
            try:
                return sanitize_for_tts(json.loads(f'"{m.group(1)}"'))
            except Exception:
                return sanitize_for_tts(m.group(1))
        return ""
    return ""


def _chat_action(text: str = "") -> dict:
    return {"tool": "chat", "params": {"response": text} if text else {}, "speak": text}


def _strip_wake_word(query: str) -> str:
    return _RE_WAKE_WORD.sub("", (query or "").strip()).strip()


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    """Fast path for blank queries; routing delegates to LLM for universal multi-lingual intent understanding."""
    if not query or not query.strip():
        return {"tool": "chat", "params": {"response": ""}, "speak": ""}
    return None


def _route_with_llm(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    messages = _build_router_messages(query, conversation_history=conversation_history)
    try:
        response = query_local_llm(
            messages,
            system_prompt="",
            max_tokens=80,
            temperature=0.0,
            repeat_penalty=1.0,
            thinking=False,
            sanitize=False,
            response_format={"type": "json_object"},
            stop=_ROUTER_STOP,
            raise_on_error=True,
        )
    except Exception as e:
        logger.error("[Tool Registry] Routing failed: %s", e)
        msg = "I ran into a problem understanding that. Could you try again?"
        return [_chat_action(msg)], msg

    tool_calls = _parse_tool_calls(response)
    final_response = _extract_final_response(response, tool_calls)

    # Smart Follow-Up Recovery: if router falls back to 'chat' on a follow-up right after
    # specialized tools (document_qa, web_search), inherit the active tool!
    if len(tool_calls) == 1 and tool_calls[0]["tool"] == "chat":
        history = _get_conv_history(conversation_history)
        resolved = rag_engine.resolve_contextual_query(query, history)
        if resolved != query.strip() and history:
            last_turn = history[-1]
            last_tool = (last_turn.get("tool") or "").strip()
            if last_tool in ("document_qa", "ask_document"):
                tool_calls = [{"tool": "document_qa", "params": {"query": resolved}, "speak": ""}]
            elif last_tool == "web_search" and is_internet_connected():
                tool_calls = [{"tool": "web_search", "params": {"query": resolved}, "speak": ""}]

    # Ensure chat tool has response/reply parameter
    for call in tool_calls:
        if call["tool"] == "chat":
            reply_val = call["params"].get("reply") or call["params"].get("response") or call.get("speak") or final_response
            if reply_val:
                call["params"]["response"] = reply_val
                call["params"]["reply"] = reply_val
                if not call.get("speak"):
                    call["speak"] = reply_val

    if tool_calls:
        return tool_calls, final_response

    return [_chat_action(final_response)], final_response


def get_agent_actions(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    if not query or not query.strip():
        return [{"tool": "chat", "params": {}, "speak": ""}], ""
    cleaned = _strip_wake_word(query) or query.strip()
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


def execute_actions(
    actions: list[dict],
    query: str = "",
    conversation_history: list | None = None,
) -> list[tuple[str, str | None, dict]]:
    results = []
    last_tool_output = ""
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = action.get("tool", "chat")
        params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
        spoken = action.get("speak", "") or params.get("speak", "") or params.get("reply", "") or params.get("response", "")

        if last_tool_output:
            for key in ("text", "context", "prompt"):
                if not params.get(key):
                    if key in params or (key == "text" and tool in ("type_text", "search_and_type", "insert_content")) or (key in ("context", "prompt") and tool in ("draft_email", "generate_content")):
                        params[key] = last_tool_output
                        break

        res_spoken, res_url, meta = execute_tool(
            tool,
            params,
            query=action.get("step_text") or query,
            spoken=spoken,
            conversation_history=conversation_history,
        )
        results.append((res_spoken, res_url, meta))

        if isinstance(meta, dict) and (meta.get("requires_confirmation") or meta.get("requires_clarification")):
            break

        out_text = (meta.get("generated_content") or meta.get("screen_text") or meta.get("text_snippet") or meta.get("text")) if isinstance(meta, dict) else ""
        last_tool_output = str(out_text or res_spoken or "").strip()

    return results


def build_action_cards(tool: str, params: dict, result_metadata: dict, url: str | None, response_text: str, prompt: str) -> list[dict]:
    cards = []
    params = params if isinstance(params, dict) else {}
    result_metadata = result_metadata if isinstance(result_metadata, dict) else {}

    if tool in ("find_file", "find_document"):
        for index, file_item in enumerate(result_metadata.get("file_results", [])):
            cards.append({
                "id": f"act-file-{index}",
                "type": "general",
                "title": file_item.get("name", "Document"),
                "subtitle": file_item.get("folder", "") or file_item.get("path", ""),
                "selected": False,
                "badge": file_item.get("extension", "file"),
                "payload": {"tool": "open_file", "path": file_item.get("path", ""), "file": file_item},
            })

    elif tool == "get_weather":
        city = str(params.get("city") or "").strip()
        w_data = _cached_weather_data(city)
        resolved_city = w_data.get("city") or (city.title() if city else "Local Area")
        cards.append({
            "id": "act-weather",
            "type": "weather",
            "title": f"Weather in {resolved_city}",
            "subtitle": response_text,
            "selected": True,
            "badge": "Weather",
            "payload": {"tool": "get_weather", "city": resolved_city, **w_data},
        })

    elif tool in ("stock_quote", "market_quote", "stock_price", "get_stock_quote") and result_metadata.get("price") is not None:
        sym = result_metadata.get("symbol", "")
        name = result_metadata.get("name", sym)
        price = result_metadata.get("price", "")
        curr = result_metadata.get("currency", "$")
        change_pct = result_metadata.get("change_pct")
        pct_str = f" ({'+' if change_pct >= 0 else ''}{change_pct:.2f}%)" if change_pct is not None else ""
        price_str = f"{price:,.2f}" if isinstance(price, (int, float)) else str(price)
        cards.append({
            "id": f"act-stock-{sym}",
            "type": "stock",
            "title": f"{name} ({sym})",
            "subtitle": response_text,
            "selected": True,
            "badge": f"{curr} {price_str}{pct_str}",
            "payload": {"tool": "stock_quote", **result_metadata},
        })

    elif tool in ("set_timer", "timer", "stopwatch"):
        is_stopwatch = tool == "stopwatch"
        parsed_secs = (
            parse_relative_seconds(str(params.get("query") or ""))
            or parse_relative_seconds(prompt)
            or parse_relative_seconds(str(params.get("duration") or params.get("seconds") or ""))
            or 0
        )
        if parsed_secs or is_stopwatch:
            label = params.get("label") or ("Stopwatch" if is_stopwatch else "Timer")
            mins, secs = int(parsed_secs // 60), int(parsed_secs % 60)
            cards.append({
                "id": "act-timer",
                "type": "stopwatch" if is_stopwatch else "timer",
                "title": "Stopwatch" if is_stopwatch else f"Timer: {label}",
                "subtitle": "Live Active Stopwatch" if is_stopwatch else f"{mins}m {secs}s countdown",
                "selected": True,
                "badge": "Stopwatch" if is_stopwatch else "Timer",
                "payload": {
                    "tool": "stopwatch" if is_stopwatch else "set_timer",
                    "duration_seconds": parsed_secs,
                    "seconds": parsed_secs,
                    "label": label,
                    "mode": "stopwatch" if is_stopwatch else "timer",
                },
            })

    elif tool in ("screen_vision", "read_screen", "ask_about_screen"):
        win_title = result_metadata.get("window_title", "Desktop")
        cards.append({
            "id": "act-screen-vision",
            "type": "screen_vision",
            "title": f"Screen Vision: {win_title}",
            "subtitle": response_text[:140] + ("..." if len(response_text) > 140 else ""),
            "selected": True,
            "badge": "Vision",
            "payload": {
                "tool": "screen_vision",
                "window_title": win_title,
                "screenshot": result_metadata.get("screenshot", "amigo_screenshot.png"),
                "snippet": result_metadata.get("text_snippet", ""),
            },
        })

    elif tool == "memory_recall" and result_metadata.get("recalled_facts"):
        facts = result_metadata.get("recalled_facts", [])
        cards.append({
            "id": "act-memory",
            "type": "memory",
            "title": "Recalled from Memory",
            "subtitle": facts[0] if facts else response_text[:120],
            "selected": True,
            "badge": "Memory",
            "payload": {"tool": "memory_recall", "facts": facts},
        })

    elif tool in ("document_qa", "ask_document") and (result_metadata.get("matched_docs") or result_metadata.get("doc_context_used")):
        docs = result_metadata.get("matched_docs", [])
        first_doc = os.path.basename(docs[0]) if docs else "Your documents"
        cards.append({
            "id": "act-doc-qa",
            "type": "document",
            "title": f"Document Knowledge: {first_doc}",
            "subtitle": response_text[:140] + ("..." if len(response_text) > 140 else ""),
            "selected": True,
            "badge": "Document",
            "payload": {"tool": "document_qa", "matched_docs": docs},
        })

    elif tool == "clarification" or result_metadata.get("requires_clarification"):
        candidates = result_metadata.get("candidates") or params.get("candidate_tools") or ["chat", "web_search"]
        query_text = result_metadata.get("query") or params.get("query") or prompt
        for idx, cand in enumerate(candidates):
            title = cand.replace("_", " ").title()
            cards.append({
                "id": f"act-clarify-{idx}",
                "type": "general",
                "title": title,
                "subtitle": f"Execute {title} for '{query_text}'",
                "selected": idx == 0,
                "badge": title,
                "actionType": "button",
                "payload": {"tool": cand, "query": query_text, "original_prompt": prompt},
            })

    elif tool == "open_website" and (url or params.get("url")):
        target_url = url or params.get("url")
        display_title = params.get("title") or result_metadata.get("title") or "Open Web Destination"
        cards.append({
            "id": f"act-url-{abs(hash(target_url)) % 100000}",
            "type": "general",
            "title": display_title,
            "subtitle": target_url,
            "selected": True,
            "badge": "Web Link",
            "payload": {"tool": "open_website", "url": target_url, "title": display_title},
        })

    return cards
