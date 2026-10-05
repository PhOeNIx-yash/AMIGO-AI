"""
Tool Registry & Action Dispatcher for Amigo Voice Assistant.
Executes agentic tool calls and generates structured UI action cards.
"""

import datetime
import json
import logging
import os
import re
import urllib.parse
import threading
import time
from typing import Any, Callable, Optional
import webbrowser

# Non-circular imports (safe at module level)
import amigo.services.os_automation as os_automation
import amigo.core.rag_engine as rag_engine
from amigo.core.reminder_timer import (
    handle_set_timer,
    handle_set_reminder,
    handle_list_reminders,
    handle_cancel_reminder,
    parse_relative_seconds,
)
from amigo.services.web_search import (searchGoogle, resolve_youtube_video, 
                           clean_search_query, search_web, format_for_llm,
                           get_stock_quote, format_quote)
from amigo.utils.settings_resolver import open_setting
from amigo.services.weather import weather_command, get_weather_data
from amigo.utils.network_utils import is_internet_connected
from amigo.services.app_opener import find_files, open_folder, open_windows_app, execute_file_action
from amigo.utils.calculate import Calc

# Forwarders to llm_agent and rag_engine
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

def _day_period(*args, **kwargs):
    from amigo.core.llm_agent import _day_period as _impl
    return _impl(*args, **kwargs)

def update_active_state(*args, **kwargs):
    from amigo.core.rag_engine import update_active_state as _impl
    return _impl(*args, **kwargs)

def get_active_state(*args, **kwargs):
    from amigo.core.rag_engine import get_active_state as _impl
    return _impl(*args, **kwargs)

# Pre-compiled regex patterns for fast matching
_RE_URLS = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]*[^\s<>"{}|\\^`\[\].,;:!?]')
_RE_APP_STRIP = re.compile(r"^(?:please\s+)?(?:open|launch|start|run|show)\s+(?:the\s+|my\s+|an?\s+)?", re.IGNORECASE)
_RE_APP_ARTICLE = re.compile(r"^(?:the|that|my|an?)\s+", re.IGNORECASE)
_RE_MEDIA_CLEAN_TITLE = re.compile(r"^(?:play|playing)\s*:\s*", re.IGNORECASE)
_RE_DOC_STRIP_ACTION = re.compile(r"^(?:please\s+)?(?:can you\s+|can u\s+|could you\s+|could u\s+|will you\s+|would you\s+)?(?:tell me\s+|give me\s+|show me\s+)?(?:open|show|read|summarize|tell me about|what is in|what does|find|locate|check|view|inspect)\s+", re.IGNORECASE)
_RE_DOC_STOPWORDS = re.compile(r"\b(?:the|that|those|these|my|a|an|file|files|document|documents|doc|pdf)\b", re.IGNORECASE)

logger = logging.getLogger("amigo.tool_registry")

# Callback to broadcast media state updates to UI
_media_update_cb = None


def set_media_update_callback(cb):
    """Set callback to broadcast media updates to UI."""
    global _media_update_cb
    _media_update_cb = cb


def extract_and_open_urls(text: str) -> bool:
    """Finds URLs in text. Auto-opening disabled for security - URLs are returned for manual clicking."""
    urls = _RE_URLS.findall(text)
    # Security: Auto-opening URLs from LLM responses is a prompt-injection risk.
    # URLs are now only returned for display; user must manually click.
    return bool(urls)


# ---------------------------------------------------------------------------
# Individual Tool Handlers
# ---------------------------------------------------------------------------

def _offline_reply(query, what):
    return get_ai_response(
        query,
        web_context=f"Error: The PC is currently offline with no internet connection. Explain to the user that {what}.",
    )


def _tool_web_search(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    call = getattr(_call_ctx, "value", None) or {}
    conv_history = call.get("conversation_history")

    if not is_internet_connected():
        reply = _offline_reply(query, "you cannot search the web right now because you are not connected to the internet")
        return reply, None, {"status": "offline", "error": "No internet connection"}

    q = str(params.get("query") or query or "").strip() or query
    target = clean_search_query(q) or q
    show_in_browser = bool(params.get("show_in_browser"))

    try:
        update_active_state("last_search", {"query": target})
    except Exception:
        pass

    # The answer prompt keeps the user's own words but adds the resolved topic, so follow-ups
    # such as "what about its population?" are answered about the right subject.
    answer_prompt = query if target.lower() in (query or "").lower() else f"{query} (the question is about: {target})"

    # Fast-mode search for voice: single engine, no content fetching, minimal processing
    search_response = search_web(target, max_results=3, fetch_content=False, fast_mode=True)
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(target)}"
    if show_in_browser:
        searchGoogle(target)

    if search_response.results:
        web_context = format_for_llm(search_response, max_chars=3000)
        response = get_ai_response(answer_prompt, web_context=web_context, conversation_history=conv_history)
        meta = {
            "status": "success",
            "query_type": search_response.query_type,
            "results_count": len(search_response.results),
            "engines_used": search_response.engines_used,
            "search_time_ms": search_response.search_time_ms,
            "structured_data": search_response.structured_data,
            "cache_hit": search_response.cache_hit,
        }
        return response, search_url, meta

    response = get_ai_response(
        answer_prompt,
        web_context="The web search returned no results. Say so honestly, and only share what you reliably know.",
        conversation_history=conv_history,
    )
    return response, search_url, {"status": "no_results"}


def _tool_stock_quote(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    target = str(params.get("symbol") or params.get("query") or query or "").strip()
    if not is_internet_connected():
        reply = _offline_reply(query, "you cannot check market or stock quotes without an internet connection")
        return reply, None, {"status": "offline", "error": "No internet connection"}

    quote = get_stock_quote(target)
    if quote:
        formatted = format_quote(quote)
        meta = {
            "status": "success",
            "symbol": quote.symbol,
            "name": quote.name,
            "price": quote.price,
            "currency": quote.currency,
            "change_pct": quote.change_pct,
            "exchange": quote.exchange,
            "url": quote.url,
        }
        return formatted, quote.url, meta

    return f"I couldn't find a live stock or market quote for '{target}'.", None, {"status": "not_found"}



def _tool_open_website(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    if not is_internet_connected():
        reply = _offline_reply(query, "websites cannot be opened or loaded without an internet connection")
        return reply, None, {"status": "offline", "error": "No internet connection"}
    raw_url = str(params.get("url") or "").strip()
    if raw_url:
        if re.match(r"^https?://", raw_url, re.I):
            url = raw_url
        elif "://" in raw_url or raw_url.lower().startswith(("javascript:", "file:", "data:")):
            return "I can only open regular web addresses.", None
        else:
            url = f"https://{raw_url}"
        webbrowser.open(url)
        try:
            update_active_state("active_subject", {"name": url, "category": "website"})
        except Exception:
            pass
        return f"Opened {url}.", url
    searchGoogle(query)
    return "Searched the web.", None


def _tool_play_youtube(params, query, spoken):
    if not is_internet_connected():
        response = get_ai_response(query, web_context="Error: The PC is currently offline with no internet connection. Explain to the user that you cannot search or stream YouTube videos without an internet connection.")
        return response, None, {"status": "offline", "error": "No internet connection"}
    q = params.get("query", query) or query
    media_info = resolve_youtube_video(q)
    clean_q = media_info.get("query") or q

    # Only set status to "playing" and report success if a URL was actually found
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

    try:
        update_active_state("current_media", {
            "title": media_payload["title"],
            "artist": media_payload["artist"],
            "platform": "YouTube",
            "query": clean_q,
            "url": media_payload["url"],
            "status": "playing",
            "stale": False,
        })
    except Exception:
        pass

    if media_info.get("url"):
        webbrowser.open(media_info["url"])

    title = media_info.get("title")
    channel = media_info.get("channel")
    if media_info.get("is_live"):
        speak_text = f"Playing live stream: '{title}'"
        if channel and channel not in ("YouTube", "YouTube Music", ""):
            speak_text += f" by {channel}"
        return speak_text + " on YouTube.", media_info.get("url")
    elif media_info.get("not_live_fallback"):
        return f"{clean_q} is not currently live. Playing their latest stream: '{title}' on YouTube.", media_info.get("url")
    elif title and title.strip().lower() != clean_q.strip().lower():
        speak_text = f"Playing '{title}'"
        if channel and channel not in ("YouTube", "YouTube Music", ""):
            speak_text += f" by {channel}"
        return speak_text + " on YouTube.", media_info.get("url")

    return spoken or f"Playing {clean_q} on YouTube.", media_info.get("url")


def _tool_get_current_media(params, query, spoken):
    # 1. Live system media session (Windows GSMTC or active window)
    live_media = _get_live_media_title_from_windows()
    is_playing = _is_system_audio_playing()

    if live_media:
        raw_title, raw_artist, status = live_media
        clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", raw_title).strip() if raw_title else ""
        by_artist = f" by {raw_artist}" if raw_artist and raw_artist not in ("YouTube Music", "YouTube", "") and raw_artist.lower() not in clean_title.lower() else ""
        
        try:
            update_active_state("current_media", {
                "title": clean_title,
                "artist": raw_artist,
                "status": status.lower(),
            })
        except Exception:
            pass

        if status.lower() == "paused":
            return f"'{clean_title}'{by_artist} is paused.", None
        return f"Currently playing '{clean_title}'{by_artist}.", None

    # 2. Audio hardware is active, check active state if valid
    if is_playing:
        state = get_active_state(clean_expired=True) if callable(get_active_state) else {}
        media_state = state.get("current_media") or {} if isinstance(state, dict) else {}
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
            return (f"I couldn't look up the time zone '{tz_name}', so here is your local time: "
                    f"{now.strftime('%I:%M %p').lstrip('0')}."), None
    return f"It is currently {now.strftime('%I:%M %p').lstrip('0')}{where}.", None


def _tool_get_date(params, query, spoken):
    now = datetime.datetime.now()
    day = str(now.day)  # No leading zero
    return f"Today is {now.strftime('%A, %B')} {day}, {now.year}.", None


def _tool_time_date(params, query, spoken):
    now = datetime.datetime.now()
    day = str(now.day)  # No leading zero
    return f"It is {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B')} {day}, {now.year}.", None


_WEATHER_CACHE: dict = {}
_WEATHER_TTL_SECONDS = 300.0


def _cached_weather_data(city: str) -> dict:
    """get_weather_data with a short cache, so the handler and the UI card share one network call."""
    key = (city or "").strip().lower()
    hit = _WEATHER_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _WEATHER_TTL_SECONDS:
        return hit[1]
    data = get_weather_data(city or "")
    if isinstance(data, dict) and data:
        _WEATHER_CACHE[key] = (time.time(), data)
        return data
    return {}


def _tool_get_weather(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    if not is_internet_connected():
        reply = _offline_reply(query, "live weather reports cannot be fetched without an internet connection")
        return reply, None, {"status": "offline", "error": "No internet connection"}
    city = str(params.get("city") or "").strip()
    # Use cached weather data to avoid duplicate network calls
    w_data = _cached_weather_data(city if city else query)
    if w_data:
        # Format a response from the cached data
        city_name = w_data.get("city", city or "your area")
        temp = w_data.get("temperature", "unknown")
        condition = w_data.get("condition", "unknown")
        result = f"Weather in {city_name}: {temp}°C, {condition}."
    else:
        result = weather_command(city if city else query) or spoken
    try:  # remember the place so a follow-up like "and tomorrow?" can reuse it
        resolved = w_data.get("city") or city
        if resolved:
            update_active_state("last_weather", {"city": resolved})
    except Exception:
        pass
    return result, None


def is_app_already_running(app_name: str) -> bool:
    """Checks if an app is already open and running on the PC."""
    target = (app_name or "").lower().strip()
    if not target or target in ("app", "window", "folder", "file"):
        return False
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower().replace(".exe", "")
            if target == name or (len(target) >= 4 and target in name):
                return True
    except Exception:
        pass
    return False


def _tool_open_app(params, query, spoken):
    app_name = (
        params.get("name")
        or params.get("app")
        or params.get("app_name")
        or params.get("target")
        or params.get("query")
        or ""
    ).strip()
    if not app_name:
        app_name = _RE_APP_STRIP.sub("", query).strip()
    if not app_name:
        return spoken or "Which application would you like to open?", None

    clean_target = _RE_APP_ARTICLE.sub("", app_name).strip()

    # 1. Is it an installed Windows application / System tool?

    if open_windows_app(clean_target):
        try:
            update_active_state("active_app", {"name": clean_target})
        except Exception:
            pass
        return get_quick_feedback(f"opened {clean_target}"), None

    # 2. Is it an already running application?
    if is_app_already_running(clean_target):
        try:
            update_active_state("active_app", {"name": clean_target})
        except Exception:
            pass
        return f"{clean_target.title()} is already open.", None

    # App not found â€” return clear status without aggressive disambiguation modal lockup
    not_found_msg = f"I couldn't find an app called '{clean_target.title()}' installed on your PC."
    return not_found_msg, None, {
        "status": "completed",
        "query": clean_target,
    }






def _tool_open_folder(params, query, spoken):
    folder_name = params.get("name", "downloads")
    ok, msg = open_folder(folder_name)
    return msg or spoken, None


def _tool_find_file(params, query, spoken):
    q = params.get("query", query)
    matches, summary = find_files(q)
    file_results = []
    for path in matches:
        try:
            stat = os.stat(path)
            file_results.append({
                "path": path,
                "name": os.path.basename(path),
                "extension": os.path.splitext(path)[1].lower() or "file",
                "folder": os.path.dirname(path),
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except (OSError, TypeError):
            continue
    if file_results:
        try:
            update_active_state("active_file", {"path": file_results[0]["path"], "name": file_results[0]["name"]})
            update_active_state("found_files", [f["path"] for f in file_results[:5]])
        except Exception:
            pass
        lines = [f"I found {len(file_results)} matching file{'s' if len(file_results) != 1 else ''}:"]
        for i, f in enumerate(file_results[:5], 1):
            clean_t = os.path.splitext(f["name"])[0].replace("_", " ").replace("-", " ").title()
            lines.append(f"{i}. {clean_t} ({f['extension']})")
        lines.append("Say 'Open number 1' or 'Open [name]' to choose.")
        summary = "\n".join(lines)
    return summary or spoken, None, {"file_results": file_results}





def _tool_take_screenshot(params, query, spoken):
    try:
        from amigo.services.screen_vision import capture_screen_image
        capture_screen_image("amigo_screenshot.png")
        return "Screenshot saved.", None
    except Exception as e:
        logger.error(f"[Screenshot] Error: {e}")
        return f"Failed to take screenshot: {e}", None


def _tool_read_screen(params, query, spoken):
    q = params.get("question", query) if isinstance(params, dict) else query
    try:
        from amigo.services.screen_vision import inspect_screen
        res = inspect_screen(q)
        reply = res.get("reply", "I inspected your screen.")
        meta = {
            "window_title": res.get("window_title", "Active Window"),
            "screenshot": res.get("screenshot_path", "amigo_screenshot.png"),
            "text_snippet": res.get("screen_text", "")[:300],
        }
        return reply, None, meta
    except Exception as e:
        logger.error(f"[Screen Vision] Error: {e}")
        return "Could not inspect the screen right now.", None


_REMEMBER_PATTERNS = [
    r"\bremember\s+(?:that|this)\b",
    r"\bsave\s+(?:that|this)\b",
    r"\bnote\s+(?:that|this)\b",
    r"\bstore\s+(?:that|this)\b",
    r"\bkeep\s+in\s+mind\s+(?:that)?\b",
]


def _legacy_extract_fact(q: str) -> str:
    """Fallback when the router did not supply an explicit fact."""
    q_low = q.lower()
    if not any(re.search(p, q_low) for p in _REMEMBER_PATTERNS):
        return ""
    fact = q
    for pattern in _REMEMBER_PATTERNS:
        fact = re.sub(pattern, "", fact, flags=re.I)
    return re.sub(r"^(?:please\s+|hey\s+amigo\s+|amigo\s+)", "", fact, flags=re.I).strip()


def _tool_memory_recall(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    q = str(params.get("query") or query or "").strip()
    q_low = q.lower()

    # 1. Store a new fact (the router passes it in `fact`; regexes are only a fallback for store operations)
    fact = str(params.get("fact") or "").strip()
    # Only run _legacy_extract_fact for store operations (when user explicitly wants to remember something),
    # not for recall questions. The router sets `fact` for store, leaves it empty for recall.
    is_store = bool(fact) or any(re.search(p, q_low) for p in _REMEMBER_PATTERNS)
    if not fact and is_store:
        fact = _legacy_extract_fact(q)
    if fact:
        try:
            rag_engine.add_user_fact(fact, category="user_memory")
            return f"I've remembered that: {fact}.", None
        except Exception as e:
            logger.error("[Memory] save failed: %s", e)
            return "I couldn't save that to memory.", None

    # 2. Recall facts or past conversations
    recalled_facts = []
    try:
        recalled = rag_engine.search(q, target_collections=[rag_engine.USER_FACTS, rag_engine.CONVERSATIONS],
                                     top_k=5, query_type="conversation_recall")
        for r in recalled:
            if r.get("score", 0) > 0.15 and r.get("text"):
                recalled_facts.append(r["text"])
    except Exception as e:
        logger.debug(f"[Memory Recall]: {e}")

    try:
        prefs = rag_engine.load_profile().get("preferences", {})
        for k, v in prefs.items():
            # match on the preference name or its exact value, never on common words like "the" or "what"
            if str(k).lower() in q_low or (str(v).strip() and str(v).lower() in q_low):
                recalled_facts.append(f"{k}: {v}")
    except Exception:
        pass

    if recalled_facts:
        mem_context = "Remembered User Facts & Conversations:\n" + "\n".join(f"- {f}" for f in recalled_facts)
        prompt = f"The user is asking: '{q}'\nUse the remembered information above to answer directly, naturally, and factually."
        return get_ai_response(prompt, doc_context=mem_context), None, {"recalled_facts": recalled_facts[:3]}

    return "I don't have anything saved about that yet.", None


def _tool_document_qa(params, query, spoken, conversation_history=None):
    params = params if isinstance(params, dict) else {}
    q = str(params.get("query") or query or "").strip() or query
    call = getattr(_call_ctx, "value", None) or {}
    conv_history = conversation_history if conversation_history is not None else call.get("conversation_history")
    try:
        doc_context = rag_engine.build_rag_context(q, top_k=5, conversation_history=conv_history)
        if doc_context:
            return get_ai_response(q, doc_context=doc_context, conversation_history=conv_history), None, {"doc_context_used": True}
    except Exception as e:
        logger.debug(f"[Document QA]: {e}")
    # Answering from general model knowledge here would invent details about the user's own files.
    return "I couldn't find anything about that in your indexed documents.", None


def _tool_type_text(params, query, spoken):
    # Require confirmation for potentially disruptive action
    text = params.get("text", "").strip() if isinstance(params, dict) else ""
    app = params.get("app", "").strip() if isinstance(params, dict) else ""
    action_desc = f"type '{text[:50]}'" + (f" into {app}" if app else "")
    confirm_result = _require_confirmation(action_desc, params, query, spoken or f"Typing {action_desc}")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    if app:
        open_windows_app(app)
        # Wait briefly for the app to gain focus before typing
        import time
        time.sleep(0.5)
    if text:
        os_automation.type_text(text)
    return (f"Typed text into {app or 'active window'}." if text else "Typed text."), None


def _tool_press_key(params, query, spoken):
    # Require confirmation for potentially disruptive action
    keys = params.get("keys", "") if isinstance(params, dict) else ""
    action_desc = f"press {keys}" if keys else "press key(s)"
    confirm_result = _require_confirmation(action_desc, params, query, spoken or f"Pressing {action_desc}")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    if keys:
        os_automation.press_shortcut(keys)
    return (f"Pressed {keys}." if keys else "Done."), None


def _tool_click_screen(params, query, spoken):
    # Require confirmation for potentially disruptive action
    import pyautogui
    x = params.get("x") if isinstance(params, dict) else None
    y = params.get("y") if isinstance(params, dict) else None
    action_desc = f"click at ({x}, {y})" if x is not None and y is not None else "click at current position"
    confirm_result = _require_confirmation(action_desc, params, query, spoken or f"Clicking {action_desc}")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    if x is not None and y is not None:
        try:
            pyautogui.click(int(x), int(y))
            return f"Clicked at ({x}, {y}).", None
        except Exception as e:
            return f"Click error: {e}", None
    try:
        pyautogui.click()
        return "Clicked.", None
    except Exception as e:
        return f"Click error: {e}", None


def _is_system_audio_playing() -> bool:
    """Checks if any application on the PC is actively outputting sound."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        for s in AudioUtilities.GetAllSessions():
            if s.Process:
                try:
                    meter = s._ctl.QueryInterface(IAudioMeterInformation)
                    if meter.GetPeakValue() > 0.0005:
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def _get_live_media_title_from_windows() -> tuple[str, str, str] | None:
    """Queries real-time live media from Windows GSMTC, falling back to window enumeration."""
    # 1. Native Windows GSMTC session (works for Chrome, Edge, Spotify even in background tabs!)
    try:
        sys_media = os_automation.get_system_media_info()
        if sys_media and sys_media.get("title"):
            status = sys_media.get("status", "playing")
            if status.lower() not in ("closed", "stopped"):
                return sys_media["title"], sys_media.get("artist", ""), status
    except Exception:
        pass

    # 2. Fallback to visible window title enumeration
    try:
        import win32gui
        titles = []

        def _enum(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                txt = win32gui.GetWindowText(hwnd).strip()
                if txt and (" - YouTube" in txt or "Spotify" in txt):
                    titles.append(txt)

        win32gui.EnumWindows(_enum, None)
        for t in titles:
            if " - YouTube" in t:
                clean = t.split(" - YouTube")[0].strip()
                if clean:
                    return clean, "", "playing"
            if "Spotify" in t and " - " in t:
                clean = t.replace("Spotify Premium", "").replace("Spotify Free", "").strip(" -")
                if clean and clean.lower() != "spotify":
                    return clean, "", "playing"
    except Exception:
        pass
    return None


def _tool_stop(params, query, spoken):
    """General stop handler: halts active speech and pauses background playback."""
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
    return "Stopped.", None


def _tool_blocked(params, query, spoken):
    """Handler for blocked/probe queries - returns empty response without calling LLM."""
    return "", None


def _tool_pause_media(params, query, spoken):
    try:
        from amigo.utils.tts import stop_speaking
        stop_speaking()
    except Exception:
        pass

    live_media = _get_live_media_title_from_windows()
    if live_media:
        _, _, status = live_media
        if status.lower() == "paused":
            return "Media is already paused.", None

    state = get_active_state(clean_expired=True)
    media = state.get("current_media") if isinstance(state, dict) else None
    has_tracked_media = isinstance(media, dict)
    is_playing = has_tracked_media and media.get("status") in ("playing", None)

    if not is_playing:
        is_playing = _is_system_audio_playing()

    if is_playing or live_media:
        try:
            os_automation.play_pause_media()
        except Exception:
            pass
        if _media_update_cb:
            _media_update_cb({"status": "paused"})
        if has_tracked_media and isinstance(media, dict):
            update_active_state("current_media", {**media, "status": "paused"})
        return "Media paused.", None

    return "No media is currently playing.", None


def _tool_play_media(params, query, spoken):
    live_media = _get_live_media_title_from_windows()
    is_system_playing = _is_system_audio_playing()

    if live_media:
        _, _, status = live_media
        if status.lower() == "playing":
            return "Media is already playing.", None
        elif status.lower() == "paused":
            try:
                os_automation.play_pause_media()
            except Exception:
                pass
            if _media_update_cb:
                _media_update_cb({"status": "playing"})
            return "Media resumed.", None

    state = get_active_state(clean_expired=False)
    media = state.get("current_media") if isinstance(state, dict) else None
    has_tracked_media = isinstance(media, dict)

    # Only send play key if media is actually paused
    is_paused = has_tracked_media and media.get("status") == "paused"

    if is_paused:
        try:
            os_automation.play_pause_media()
        except Exception:
            pass
        if _media_update_cb:
            _media_update_cb({"status": "playing"})
        if has_tracked_media and isinstance(media, dict):
            update_active_state("current_media", {**media, "status": "playing"})
        return "Media resumed.", None

    if is_system_playing:
        return "Media is already playing.", None

    return "No media is currently paused to resume.", None


def _mark_media_stale():
    """After skipping tracks the stored title is no longer reliable; say so instead of repeating it."""
    try:
        media = get_active_state(clean_expired=False).get("current_media")
        if isinstance(media, dict) and media:
            update_active_state("current_media", {**media, "stale": True, "status": "playing"})
    except Exception:
        pass


def _tool_next_track(params, query, spoken):
    os_automation.next_track()
    _mark_media_stale()
    return "Next track.", None


def _tool_prev_track(params, query, spoken):
    os_automation.prev_track()
    _mark_media_stale()
    return "Previous track.", None


def _tool_close_app(params, query, spoken):
    # Require confirmation for potentially disruptive action
    confirm_result = _require_confirmation("Close app", params, query, spoken or "Closing app")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    name = params.get("app_name") or params.get("name") or params.get("app") or ""
    closed = os_automation.close_app(name)
    if not name or name.lower() in ("current", "active", "this", "window", "app", "application", "it"):
        return "Window closed.", None
    if closed:
        return f"Closed {name.title()}.", None
    return f"{name.title()} is not currently running.", None


def _tool_window_management(params, query, spoken):
    action = params.get("action", "")
    app_name = params.get("app_name", "") or params.get("name", "")
    # Only close if action is explicitly close_window or close_app
    if action in ("close_window", "close_app"):
        return _tool_close_app({"app_name": app_name}, query, spoken)
    elif action:
        os_automation.window_action(action)
    return "Done.", None


def _tool_calculate(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    expr = str(params.get("expression") or query or "").strip()
    if expr:
        try:
            res = Calc(expr)
        except Exception:
            res = None
        if res is not None:
            return f"The answer is {res}.", None
    # Calc could not evaluate it (e.g. a word problem): let the model work it out instead of faking an answer.
    return get_ai_response(f"Solve this and give the answer with a one-line explanation: {expr or query}", use_memory=False), None


def _tool_clarification(params, query, spoken):
    """Handles clarifying questions when intent or parameters are ambiguous."""
    candidates = params.get("candidate_tools", []) if isinstance(params, dict) else []
    q = params.get("query", query) if isinstance(params, dict) else query
    msg = f"I'm not completely sure what you'd like to do with '{q}'. Please choose an option below or clarify."
    return msg, None, {
        "status": "requires_clarification",
        "requires_clarification": True,
        "candidates": candidates,
        "query": q,
    }


_call_ctx = threading.local()
_PENDING_CONFIRMATION = None
_PENDING_TTL_SECONDS = 90.0


def get_pending_confirmation():
    """The action Amigo is waiting for a yes/no on, or None (expires after a short time)."""
    global _PENDING_CONFIRMATION
    pending = _PENDING_CONFIRMATION
    if pending and (time.time() - pending["created"]) > _PENDING_TTL_SECONDS:
        _PENDING_CONFIRMATION = None
        return None
    return pending


def _pending_confirmation_line() -> str:
    """Format prompt hint when an action is awaiting user confirmation."""
    pending = get_pending_confirmation()
    if not pending:
        return ""
    action_name = pending.get("action_name") or pending.get("tool") or "action"
    return f"[Awaiting user confirmation: user was asked to confirm '{action_name}']"


def _require_confirmation(action_name: str, params: dict, query: str, spoken: str, tool: str | None = None) -> tuple[str, None, dict]:
    """Ask the user to confirm a risky action. Returns a 3-tuple whose metadata marks it as pending.

    `confirmed` only counts when it is the boolean True and arrives through confirm_action or the UI;
    the agent strips it from model output so the model cannot approve its own actions.
    """
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


def _tool_chat(params, query, spoken, conversation_history=None):
    """Conversational reply with full context and conversation history."""
    params = params if isinstance(params, dict) else {}
    direct = (spoken or params.get("response") or "").strip()
    if direct:
        return direct, None

    call = getattr(_call_ctx, "value", None) or {}
    conv_history = conversation_history if conversation_history is not None else call.get("conversation_history")

    doc_context = ""
    try:
        # Check if the query has a high-confidence match in indexed documents or user facts
        results = rag_engine.search(query, target_collections=[rag_engine.DOCUMENTS, rag_engine.USER_FACTS], top_k=3)
        if results and any(r.get("score", 0) >= 0.35 or r.get("rerank_score", 0) >= 0.35 for r in results):
            doc_context = rag_engine.build_rag_context(query, top_k=3, conversation_history=conv_history)
    except Exception as e:
        logger.debug(f"[Chat RAG context]: {e}")

    return get_ai_response(query, doc_context=doc_context, conversation_history=conv_history), None


def _tool_generate_content(params, query, spoken):
    """Generate content (email, letter, code, ...) and return it for panel display."""
    params = params if isinstance(params, dict) else {}
    content_type = params.get("type", "text")
    topic = params.get("topic", query)
    context = params.get("context", "")

    generation_prompt = (
        f"Generate a {content_type} about: {topic}\n\n"
        f"Context: {context}\n\n"
        f"Write a professional, well-structured {content_type}. Be concise but complete. "
        "Do not include meta-commentary or explanations - just the content itself."
    )
    # Generated documents are not spoken replies: no chat history, no 1-3 sentence limit, longer budget.
    generated_content = get_ai_response(generation_prompt, use_memory=False, is_voice=False, max_tokens=1500)

    # Store generated content in active state for insert_content to use
    try:
        update_active_state("generated_content", {
            "content": generated_content,
            "type": content_type,
            "topic": topic,
        })
    except Exception:
        pass

    return f"Generated {content_type} about {topic}.", None, {
        "generated_content": generated_content,
        "content_type": content_type,
        "topic": topic,
        "showGeneratedPanel": True,
    }


def _tool_insert_content(params, query, spoken):
    """Insert generated content into active window via clipboard paste.
    Uses try/finally to ensure clipboard is always restored."""
    # Require confirmation for potentially disruptive action
    confirm_result = _require_confirmation("Insert content", params, query, spoken or "Inserting content")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    
    content = params.get("content", "")
    # If no content provided, try to get the last generated content from active state
    if not content:
        try:
            state = get_active_state(clean_expired=False)
            generated = state.get("generated_content", {})
            content = generated.get("content", "")
        except Exception:
            pass
    if not content:
        return "No content to insert.", None
    
    import pyperclip
    import pyautogui
    import time
    
    # Save current clipboard
    old_clipboard = ""
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        pass
    
    try:
        # Set new content
        pyperclip.copy(content)
        # Paste
        pyautogui.hotkey("ctrl", "v")
        # Wait for paste to complete - use a longer delay and check
        time.sleep(0.3)
        return "Content inserted successfully.", None
    except Exception as e:
        logger.error(f"[Insert Content] Error: {e}")
        return f"Failed to insert content: {e}", None
    finally:
        # Always restore clipboard, even if paste failed
        try:
            if old_clipboard:
                pyperclip.copy(old_clipboard)
        except Exception:
            pass



# ---------------------------------------------------------------------------
# Simple Automation Action Map
# (Function to call, default spoken message)
# ---------------------------------------------------------------------------
_SIMPLE_OS_ACTIONS = {
    "scroll_down":        (os_automation.scroll_down, ""),
    "scroll_up":          (os_automation.scroll_up, ""),
    "close_tab":          (os_automation.close_tab, "Closing tab."),
    "next_tab":           (os_automation.next_tab, ""),
    "prev_tab":           (os_automation.prev_tab, ""),
    "volume_up":          (os_automation.volume_up, "Volume increased."),
    "volume_down":        (os_automation.volume_down, "Volume decreased."),
    "mute":               (os_automation.mute, "Audio muted."),
    "lock_pc":            (os_automation.lock_pc, "Locking your PC."),
    "sleep_pc":           (os_automation.sleep_pc, "Putting system to sleep."),
    "cancel_shutdown":    (os_automation.cancel_shutdown, "Shutdown cancelled."),
}


def _make_simple_handler(func, default_msg):
    def _handler(params, query, spoken):
        func()
        return default_msg, None
    return _handler


def _tool_empty_recycle_bin(params, query, spoken):
    # Require confirmation for destructive action
    confirm_result = _require_confirmation("Empty recycle bin", params, query, "Emptying recycle bin")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    os_automation.empty_recycle_bin()
    return "Recycle bin emptied.", None


def _tool_lock_pc(params, query, spoken):
    # Require confirmation for potentially disruptive action
    confirm_result = _require_confirmation("Lock PC", params, query, "Locking PC")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    os_automation.lock_pc()
    return "Locking your PC.", None


def _tool_sleep_pc(params, query, spoken):
    # Require confirmation for potentially disruptive action
    confirm_result = _require_confirmation("Sleep PC", params, query, "Putting system to sleep")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    os_automation.sleep_pc()
    return "Putting system to sleep.", None


# ---------------------------------------------------------------------------
# Dynamic Document Resolver & File Actions
# ---------------------------------------------------------------------------

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_REFERENT_WORDS = frozenset({"that", "it", "this", "those", "them", "these", "one", "ones", "selected", "same", "number", "no"})


def _ordinal_index(text: str, count: int):
    """Index into the last found-files list for 'number 3', '2nd', 'the first one', 'the last one'."""
    low = text.lower()
    if re.search(r"\blast\s+one\b", low):
        return count - 1
    m = re.search(r"\b(?:number|no\.?|#)\s*(\d+)\b", low) or re.search(r"\b(\d+)(?:st|nd|rd|th)\b", low)
    if m:
        n = int(m.group(1))
        return n - 1 if 1 <= n <= count else -1  # -1 = the user named a position that does not exist
    for word, n in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            return n - 1 if n <= count else -1
    return None


def resolve_document_path(query_or_target: str) -> str | None:
    """
    Resolve a local file path from a name, a pronoun ("it", "that"), or an ordinal ("the second one").
    Order: direct path -> ordinal from the last search -> pronoun -> filename search -> semantic search.
    Returns None when nothing matches; it never silently falls back to an unrelated earlier file.
    """
    state = get_active_state(clean_expired=False) or {}
    active_f = state.get("active_file")
    active_path = active_f.get("path") if isinstance(active_f, dict) else None
    active_ok = bool(active_path and os.path.exists(active_path))

    if not query_or_target or not isinstance(query_or_target, str):
        return active_path if active_ok else None

    raw = query_or_target.strip()
    if os.path.isfile(raw):
        return raw

    clean = _RE_DOC_STRIP_ACTION.sub("", raw)
    clean = re.sub(r"\s+", " ", _RE_DOC_STOPWORDS.sub("", clean)).strip()

    found_list = state.get("found_files", [])
    if isinstance(found_list, list) and found_list:
        idx = _ordinal_index(raw, len(found_list))
        if idx == -1:
            return None  # e.g. 'number 10' when only 3 files were found: don't guess another file
        if idx is not None and os.path.exists(found_list[idx]):
            return found_list[idx]

    tokens = re.findall(r"[a-z0-9']+", clean.lower())
    if not tokens or all(t in _REFERENT_WORDS or t in _ORDINAL_WORDS or t.isdigit() for t in tokens):
        return active_path if active_ok else None

    search_terms = []
    # 1. Match explicit file extension (e.g. eticket.pdf, invoice.xlsx)
    m_ext = re.search(r'\b([\w\-.]+\.(?:pdf|docx?|txt|xlsx?|csv|json|py|pptx?|html))\b', raw, re.I)
    if m_ext:
        search_terms.append(m_ext.group(1))

    # 2. Match prepositional file reference (e.g. in invoice, from eticket) - run on clean, not raw
    m_doc = re.search(r"\b(?:file|document|doc|ticket|sheet|in|from|of)\s+([a-zA-Z0-9_\-]{2,25})", clean, re.I)
    if m_doc:
        candidate = m_doc.group(1).strip()
        # Skip ordinal words and referent words
        if candidate and candidate not in search_terms and candidate.lower() not in _REFERENT_WORDS and candidate.lower() not in _ORDINAL_WORDS:
            search_terms.append(candidate)

    # 3. Only search clean string if it is short (1-2 tokens)
    if not search_terms and len(clean.split()) <= 2 and len(clean) >= 3:
        search_terms.append(clean)

    for term in search_terms:  # 1. filename lookup
        if len(term) >= 2:
            matches, _ = find_files(term)
            if matches:
                update_active_state("active_file", {"path": matches[0], "name": os.path.basename(matches[0])})
                return matches[0]

    for term in search_terms:  # 2. semantic lookup
        if len(term) >= 3 and term.lower() not in _REFERENT_WORDS:
            results = rag_engine.search_files_by_context(term, top_k=1)
            if results and results[0].get("score", 0) > 0.30:
                fpath = results[0].get("filepath")
                if fpath:
                    update_active_state("active_file", {"path": fpath, "name": os.path.basename(fpath)})
                    return fpath

    return None


def _tool_file_action(params, query, spoken, default_action="open"):
    """Run an action (open / reveal / copy_path) on a local file and report the real outcome."""
    params = params if isinstance(params, dict) else {}
    target = params.get("path") or params.get("name") or query
    action = params.get("action") or default_action
    target_path = resolve_document_path(str(target))

    if not (target_path and os.path.exists(target_path)):
        return "I couldn't find that file on this computer.", None

    try:
        update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
    except Exception:
        pass
    ok, msg = execute_file_action(target_path, action)
    if not ok:
        return msg or f"I couldn't {str(action).replace('_', ' ')} that file.", None
    clean_name = os.path.splitext(os.path.basename(target_path))[0].replace("_", " ").replace("-", " ").title()
    if action == "open":
        return get_quick_feedback(f"opened {clean_name}"), None
    return msg or f"Done with {clean_name}.", None


def _make_file_handler(default_action):
    def _handler(params, query, spoken):
        return _tool_file_action(params, query, spoken, default_action=default_action)
    return _handler


def _tool_show_images(params, query, spoken):
    return spoken or get_quick_feedback("showing image results"), "https://www.google.com/search?q=" + urllib.parse.quote(params.get("query", query))

def _tool_search_and_type(params, query, spoken):
    # Require confirmation for potentially disruptive action
    confirm_result = _require_confirmation("Search and type", params, query, spoken or "Searching and typing")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    os_automation.search_and_type(params.get("text", ""))
    return spoken, None

def _tool_new_tab(params, query, spoken):
    os_automation.new_tab(params.get("url", ""))
    return spoken, None


def _level_result(res, default_msg):
    if isinstance(res, (tuple, list)):
        return res[-1]
    if isinstance(res, str):
        return res
    return default_msg


def _tool_set_volume(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    action = str(params.get("action") or "").strip().lower()
    level = params.get("level")
    has_level = level not in (None, "")

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

    if not has_level:
        return "What volume level would you like, from 0 to 100?", None
    res = os_automation.set_volume(level)
    return _level_result(res, f"Volume set to {level} percent."), None


def _tool_set_brightness(params, query, spoken):
    params = params if isinstance(params, dict) else {}
    level = params.get("level")
    if level in (None, ""):
        return "What brightness level would you like, from 0 to 100?", None
    res = os_automation.set_brightness(level)
    return _level_result(res, f"Brightness set to {level} percent."), None


def _tool_open_settings(params, query, spoken):
    return open_setting(params.get("setting", "")), None

def _tool_system_status(params, query, spoken):
    return os_automation.get_system_status().get("summary", "System status checked."), None


def _tool_system_control(params, query, spoken):
    """Handle various system control actions."""
    action = params.get("action", "").lower() if isinstance(params, dict) else ""
    
    if action in ("lock", "lock_pc"):
        return _tool_lock_pc(params, query, "Locking PC")
    elif action in ("sleep", "sleep_pc"):
        return _tool_sleep_pc(params, query, "Putting system to sleep")
    elif action in ("restart", "restart_pc"):
        return _tool_restart_pc(params, query, "Restarting PC in 30 seconds")
    elif action in ("shutdown", "shutdown_pc"):
        # Shutdown would be very destructive - require explicit confirmation
        confirm_result = _require_confirmation("Shutdown PC", params, query, "Shutting down PC")
        if confirm_result[2].get("requires_confirmation"):
            return confirm_result
        shutdown_fn = getattr(os_automation, "shutdown_pc", None)
        if callable(shutdown_fn):
            shutdown_fn(30)
        else:
            os.system("shutdown /s /t 30")
        return "Shutting down in 30 seconds.", None
    elif action in ("cancel_shutdown",):
        os_automation.cancel_shutdown()
        return "Shutdown cancelled.", None
    elif action in ("empty_recycle_bin",):
        return _tool_empty_recycle_bin(params, query, "Emptying recycle bin")
    else:
        return f"Unknown system control action: {action}", None

def _tool_restart_pc(params, query, spoken):
    # Require confirmation for destructive action
    confirm_result = _require_confirmation("Restart PC", params, query, "Restarting PC in 30 seconds")
    if confirm_result[2].get("requires_confirmation"):
        return confirm_result
    os_automation.restart_pc(30)
    return "Restarting in 30 seconds.", None

def _tool_set_timer(params, query, spoken):
    return handle_set_timer(params, query), None

def _tool_set_reminder(params, query, spoken):
    return handle_set_reminder(params, query), None

def _tool_list_reminders(params, query, spoken):
    return handle_list_reminders(), None

def _tool_cancel_reminder(params, query, spoken):
    return handle_cancel_reminder(params), None

def _tool_exit(params, query, spoken):
    return "Goodbye!", None


# ---------------------------------------------------------------------------
# Unified Tool Handlers Dictionary
# ---------------------------------------------------------------------------
UI_TOOL_HANDLERS = {
    "web_search":        _tool_web_search,
    "open_website":      _tool_open_website,
    "show_images":       _tool_show_images,
    "play_youtube":      _tool_play_youtube,
    "get_time":          _tool_get_time,
    "get_date":          _tool_get_date,
    "get_weather":       _tool_get_weather,
    "open_app":          _tool_open_app,
    "take_screenshot":   _tool_take_screenshot,
    "read_screen":       _tool_read_screen,
    "ask_about_screen":  _tool_read_screen,
    "type_text":         _tool_type_text,
    "search_and_type":   _tool_search_and_type,
    "press_key":         _tool_press_key,
    "click_screen":      _tool_click_screen,
    "new_tab":           _tool_new_tab,
    "close_app":         _tool_close_app,
    "window_management": _tool_window_management,
    "window_mgmt": _tool_window_management,
    "calculate":         _tool_calculate,
    "set_volume":        _tool_set_volume,
    "set_brightness":    _tool_set_brightness,
    "open_settings":     _tool_open_settings,
    "system_status":     _tool_system_status,
    "hardware_metrics":  _tool_system_status,
    "system_control":    _tool_system_control,
    "restart_pc":        _tool_restart_pc,
    "pause_media":       _tool_pause_media,
    "play_media":        _tool_play_media,
    "resume_media":      _tool_play_media,
    "resume_playback":   _tool_play_media,
    "stock_quote":       _tool_stock_quote,
    "get_stock_quote":   _tool_stock_quote,
    "market_quote":      _tool_stock_quote,
    "stock_price":       _tool_stock_quote,
    "stop":              _tool_stop,
    "stop_speaking":     _tool_stop,
    "next_track":        _tool_next_track,
    "prev_track":        _tool_prev_track,
    "set_timer":         _tool_set_timer,
    "stopwatch":         _tool_set_timer,
    "set_reminder":      _tool_set_reminder,
    "list_reminders":    _tool_list_reminders,
    "cancel_reminder":   _tool_cancel_reminder,
    "open_folder":       _tool_open_folder,
    "find_file":         _tool_find_file,
    "open_file":         _tool_file_action,
    "reveal_file":       _tool_file_action,
    "copy_file_path":    _tool_file_action,
    "screen_vision":     _tool_read_screen,
    "memory_recall":     _tool_memory_recall,
    "document_qa":       _tool_document_qa,
    "current_media":     _tool_get_current_media,
    "get_current_media": _tool_get_current_media,
    "clarification":     _tool_clarification,
    "time_date":         _tool_get_time,
    "exit":              _tool_exit,
    "chat":              _tool_chat,
    "generate_content":  _tool_generate_content,
    "insert_content":    _tool_insert_content,
    "empty_recycle_bin": _tool_empty_recycle_bin,
    "lock_pc":           _tool_lock_pc,
    "sleep_pc":          _tool_sleep_pc,
    "blocked":           _tool_blocked,
}


# ---------------------------------------------------------------------------
# Dynamic Document Resolver & RAG Handlers
# ---------------------------------------------------------------------------


def _tool_ask_document(params, query, spoken):
    """RAG Q&A on a specific document (when the router names one) or on the whole knowledge base."""
    params = params if isinstance(params, dict) else {}
    filepath = str(params.get("filepath") or "").strip()
    question = str(params.get("question") or query or "").strip() or query

    target_path = resolve_document_path(filepath) if filepath else None
    if target_path:
        try:
            update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        except Exception:
            pass
        ctx = rag_engine.build_file_context(target_path, question)
    else:
        ctx = rag_engine.build_rag_context(question, top_k=6)

    if ctx:
        meta = {"matched_docs": [target_path]} if target_path else {"doc_context_used": True}
        return get_ai_response(question, doc_context=ctx), None, meta
    return "I couldn't find anything relevant in your documents.", None


def _tool_summarize_document(params, query, spoken):
    """Summarize a document using RAG + LLM."""
    filepath = params.get("filepath", "") if isinstance(params, dict) else ""
    target_path = resolve_document_path(filepath) if filepath else resolve_document_path(query)

    if target_path:
        try:
            update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        except Exception:
            pass
        ctx = rag_engine.get_file_summary_context(target_path)
    else:
        ctx = rag_engine.build_rag_context(query, top_k=6)

    if ctx:
        target_prompt = query.strip() if query.strip() else "Summarize this document concisely."
        return get_ai_response(target_prompt, doc_context=ctx), None
    return "I couldn't find that document to summarize.", None


def _tool_find_document(params, query, spoken):
    """Semantic file discovery â€” find files by meaning, not just name."""
    q = params.get("query", query).strip()
    results = rag_engine.search_files_by_context(q, top_k=10)
    if results:
        file_results = []
        for r in results:
            try:
                stat = os.stat(r["filepath"])
                file_results.append({
                    "path": r["filepath"],
                    "name": r["filename"],
                    "extension": os.path.splitext(r["filepath"])[1].lower() or "file",
                    "folder": os.path.dirname(r["filepath"]),
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "score": r["score"],
                    "preview": r["preview"][:100],
                })
            except (OSError, TypeError):
                continue
        if file_results:
            try:
                update_active_state("active_file", {"path": file_results[0]["path"], "name": file_results[0]["name"]})
                update_active_state("found_files", [f["path"] for f in file_results[:5]])
            except Exception:
                pass
            count = len(file_results)
            lines = [f"I found {count} matching file{'s' if count != 1 else ''}:"]
            for i, f in enumerate(file_results[:5], 1):
                clean_t = os.path.splitext(f["name"])[0].replace("_", " ").replace("-", " ").title()
                lines.append(f"{i}. {clean_t} ({f['extension']})")
            lines.append("Say 'Open number 1' or 'Open [name]' to choose.")
            summary = "\n".join(lines)
            return summary, None, {"file_results": file_results}

    return f"No files found matching '{q}'. You can re-index your documents in Settings.", None



def _tool_search_knowledge(params, query, spoken):
    """Search across ALL indexed knowledge (docs, memory, email, calendar)."""
    q = params.get("query", query).strip()
    ctx = rag_engine.build_rag_context(q, top_k=5)
    if ctx:
        return get_ai_response(q, doc_context=ctx), None
    return get_ai_response(q), None


def _tool_read_emails(params, query, spoken):
    """Read recent emails from Outlook."""
    try:
        from amigo.services.mail_integration import get_email_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available. Please make sure Microsoft Outlook is installed and running.", None
        count = int(params.get("count", 5))
        summary = get_email_summary_text(count)
        return get_ai_response(f"Summarize these emails naturally:\n{summary}", use_memory=False), None
    except Exception as e:
        logger.error("[Email] %s", e)
        return "Could not read emails. Make sure Outlook is running.", None


def _tool_search_emails(params, query, spoken):
    """Search emails by keyword, with fallback to RAG indexed knowledge."""
    try:
        from amigo.services.mail_integration import search_emails, is_outlook_available
        q = params.get("query", query).strip()
        results = []
        if is_outlook_available():
            results = search_emails(q, max_results=5)

        if results:
            lines = []
            for i, e in enumerate(results, 1):
                lines.append(f"{i}. From: {e['sender']} | Subject: {e['subject']}")
            return f"Found {len(results)} email{'s' if len(results)>1 else ''} matching '{q}':\n" + "\n".join(lines), None

        # Fallback: search indexed RAG documents / files / knowledge
        rag_ctx = rag_engine.build_rag_context(query or q, top_k=5)
        if rag_ctx:
            return get_ai_response(query or q, doc_context=rag_ctx), None

        return f"No emails or documents found matching '{q}'.", None
    except Exception as e:
        logger.error("[Email Search] %s", e)
        try:
            rag_ctx = rag_engine.build_rag_context(query or params.get("query", ""), top_k=5)
            if rag_ctx:
                return get_ai_response(query or params.get("query", ""), doc_context=rag_ctx), None
        except Exception:
            pass
        return "Could not search emails.", None


def _tool_unread_emails(params, query, spoken):
    """Get unread email count and previews."""
    try:
        from amigo.services.mail_integration import get_unread_count, get_unread_emails, is_outlook_available
        if not is_outlook_available():
            return "Outlook is not available.", None
        count = get_unread_count()
        if count <= 0:
            return "You have no unread emails.", None
        emails = get_unread_emails(min(count, 5))
        lines = [f"You have {count} unread email{'s' if count > 1 else ''}."]
        for i, e in enumerate(emails, 1):
            lines.append(f"  {i}. From {e['sender']}: {e['subject']}")
        return "\n".join(lines), None
    except Exception as e:
        logger.error("[Unread] %s", e)
        return "Could not check unread emails.", None


def _tool_draft_email(params, query, spoken):
    """Draft an email using LLM to generate content."""
    try:
        from amigo.services.mail_integration import draft_email
        to = params.get("to", "").strip()
        subject = params.get("subject", "").strip()
        prompt = params.get("prompt", query).strip()
        # Generate email body using LLM
        body = get_ai_response(
            f"Write a professional email. Context: {prompt}. "
            f"To: {to or 'recipient'}. Subject: {subject or 'as appropriate'}. "
            f"Write ONLY the email body, no subject line or greeting prefix.",
            use_memory=False,
        )
        result = draft_email(to=to, subject=subject, body=body)
        return result, None
    except Exception as e:
        logger.error("[Draft] %s", e)
        return "Could not create email draft.", None


def _tool_get_calendar(params, query, spoken):
    """Get today's or upcoming calendar events."""
    try:
        from amigo.services.calendar_integration import get_calendar_summary_text, get_upcoming_summary_text, is_outlook_available
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None
        days = int(params.get("days", 1))
        if days <= 1:
            summary = get_calendar_summary_text()
        else:
            summary = get_upcoming_summary_text(days=days)
        return summary, None
    except Exception as e:
        logger.error("[Calendar] %s", e)
        return "Could not read calendar.", None


def _tool_search_calendar(params, query, spoken):
    """Search calendar events by keyword."""
    try:
        from amigo.services.calendar_integration import search_events, is_outlook_available
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None
        q = params.get("query", query).strip()
        results = search_events(q, days=30)
        if results:
            lines = [f"Found {len(results)} event{'s' if len(results)>1 else ''} matching '{q}':"]
            for i, e in enumerate(results, 1):
                loc = f" at {e['location']}" if e.get('location') else ""
                lines.append(f"  {i}. {e['subject']}{loc} â€” {e['start']}")
            return "\n".join(lines), None
        return f"No calendar events found matching '{q}'.", None
    except Exception as e:
        logger.error("[Calendar Search] %s", e)
        return "Could not search calendar.", None


# Register RAG/Email/Calendar tools
UI_TOOL_HANDLERS.update({
    "ask_document":       _tool_ask_document,
    "summarize_document": _tool_summarize_document,
    "find_document":      _tool_find_document,
    "search_knowledge":   _tool_search_knowledge,
    "read_emails":        _tool_read_emails,
    "search_emails":      _tool_search_emails,
    "unread_emails":      _tool_unread_emails,
    "draft_email":        _tool_draft_email,
    "get_calendar":       _tool_get_calendar,
    "search_calendar":    _tool_search_calendar,
})

# Bind simple OS automation actions
for _key, (_func, _msg) in _SIMPLE_OS_ACTIONS.items():
    UI_TOOL_HANDLERS.setdefault(_key, _make_simple_handler(_func, _msg))  # keep confirmation handlers for lock/sleep


def _tool_confirm_action(params, query, spoken):
    """Run the action the user just agreed to."""
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


UI_TOOL_HANDLERS.update({
    "confirm_action": _tool_confirm_action,
    "cancel_action":  _tool_cancel_action,
    "time_date":      _tool_time_date,
    "reveal_file":    _make_file_handler("reveal"),
    "copy_file_path": _make_file_handler("copy_path"),
})


# ---------------------------------------------------------------------------
# Tool schema shown to the router LLM. This is the single place that tells the model what each
# tool is for, so routing knowledge lives here rather than in prompt examples.
# ---------------------------------------------------------------------------

def _p(type_: str, description: str = "") -> dict:
    return {"type": type_, "description": description}


def _tool(name: str, description: str, props: dict | None = None, required: list | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []},
    }


TOOL_DEFINITIONS = [
    _tool("chat", "Conversational response: talk with the user, answer questions, provide explanations, opinions, greetings, advice, jokes, writing help, or general conversation."),
    _tool("web_search", "Search the web for news, real-time facts, current events, definitions, people, or external knowledge.",
          {"query": _p("string", "Search query resolved from context"),
           "show_in_browser": _p("boolean", "True if user asked to open search results in a browser")}, ["query"]),
    _tool("stock_quote", "Look up real-time stock prices, share prices, market quotes, or cryptocurrency rates.",
          {"symbol": _p("string", "Company name, ticker symbol, or cryptocurrency name")}, ["symbol"]),
    _tool("open_website", "Open a specific website or URL in the browser.", {"url": _p("string", "Website address or domain")}, ["url"]),
    _tool("show_images", "Show image search results for a topic in the browser.", {"query": _p("string", "Image search topic")}, ["query"]),
    _tool("play_youtube", "Search and play songs, music, artist tracks, albums, or videos on YouTube.",
          {"query": _p("string", "Song, artist, or video title to play")}, ["query"]),
    _tool("resume_media", "Resume or unpause paused audio or video playback (e.g. 'resume', 'unpause', 'continue playback')."),
    _tool("pause_media", "Pause currently playing music or video playback."),
    _tool("next_track", "Skip to the next music track."),
    _tool("prev_track", "Go back to the previous track."),
    _tool("current_media", "Check which song or video is currently playing."),
    _tool("set_volume", "Control system audio volume (mute, unmute, volume_up, volume_down, or set_volume with level).",
          {"action": _p("string", "mute | unmute | volume_up | volume_down | set_volume"),
           "level": _p("integer", "Volume level 0-100 when action is set_volume")}),
    _tool("set_brightness", "Adjust screen brightness.", {"level": _p("integer", "Brightness level 0-100")}, ["level"]),
    _tool("take_screenshot", "Capture a screenshot of the computer screen to an image file."),
    _tool("read_screen", "Inspect the current screen content or active window and answer questions about it.",
          {"question": _p("string", "Question about the screen content")}),
    _tool("open_app", "Launch or open an installed desktop application.",
          {"name": _p("string", "Application name to open")}, ["name"]),
    _tool("close_app", "Close an application or active window.",
          {"name": _p("string", "Application name to close, or empty for active window")}),
    _tool("window_mgmt", "Manage desktop windows (minimize_all, maximize, restore, switch_window, close_window).",
          {"action": _p("string", "minimize_all | maximize | restore | switch_window | close_window"),
           "name": _p("string", "Application or window title")}, ["action"]),
    _tool("lock_pc", "Lock the computer screen."),
    _tool("sleep_pc", "Put the computer to sleep."),
    _tool("restart_pc", "Restart the computer."),
    _tool("system_control", "System power management actions.",
          {"action": _p("string", "shutdown | cancel_shutdown | lock | sleep | restart")}, ["action"]),
    _tool("empty_recycle_bin", "Empty the Windows Recycle Bin."),
    _tool("system_status", "Check system hardware status (battery, CPU, memory, disk)."),
    _tool("open_settings", "Open a Windows settings page.", {"setting": _p("string", "Target setting page")}, ["setting"]),
    _tool("get_time", "Check the current time, optionally for a specific timezone.",
          {"timezone": _p("string", "IANA timezone or region")}),
    _tool("get_date", "Check today's date."),
    _tool("get_weather", "Get the weather forecast for a city or local area.",
          {"city": _p("string", "City or location name resolved from context")}),
    _tool("calculate", "Evaluate a mathematical expression or arithmetic calculation.",
          {"expression": _p("string", "Math expression to evaluate")}, ["expression"]),
    _tool("set_timer", "Start a countdown timer.", {"query": _p("string", "Timer duration or description")}, ["query"]),
    _tool("set_reminder", "Create a task reminder.", {"query": _p("string", "Reminder content and time")}, ["query"]),
    _tool("list_reminders", "List pending reminders."),
    _tool("cancel_reminder", "Cancel one or all reminders.", {"query": _p("string", "'all' or specific reminder title")}, ["query"]),
    _tool("find_file", "Search for local files or folders on the computer by name.",
          {"query": _p("string", "File or folder name pattern")}, ["query"]),
    _tool("find_document", "Search documents by meaning or content (semantic search).",
          {"query": _p("string", "Document topic or keywords")}, ["query"]),
    _tool("open_file", "Open a local file by path or name.",
          {"path": _p("string", "File path or name resolved from context")}, ["path"]),
    _tool("reveal_file", "Show a file in File Explorer.",
          {"path": _p("string", "File path or name")}, ["path"]),
    _tool("copy_file_path", "Copy a file's full path to clipboard.", {"path": _p("string", "File path")}, ["path"]),
    _tool("open_folder", "Open a folder in File Explorer.", {"name": _p("string", "Folder name or path")}, ["name"]),
    _tool("document_qa", "Answer questions from the user's indexed local documents, files, tickets, bookings, PNR numbers, or personal records.",
          {"query": _p("string", "Question about document or personal file contents")}, ["query"]),
    _tool("ask_document", "Answer questions about a specific document.",
          {"question": _p("string", "Question"), "filepath": _p("string", "Target document path or name")}, ["question"]),
    _tool("summarize_document", "Summarize the contents of a local document.",
          {"filepath": _p("string", "Document path or name")}),
    _tool("memory_recall", "Store a user fact or recall saved personal information.",
          {"fact": _p("string", "Fact to remember (leave empty if recalling)"),
           "query": _p("string", "Recall query")}),
    _tool("get_calendar", "View calendar events.", {"days": _p("integer", "Number of days ahead (default 1)")}),
    _tool("search_calendar", "Search calendar events by keyword.", {"query": _p("string", "Search keyword")}, ["query"]),
    _tool("unread_emails", "Check unread emails in Outlook."),
    _tool("read_emails", "Read and summarize recent emails.", {"count": _p("integer", "Number of emails to read")}),
    _tool("search_emails", "Search emails by keyword.", {"query": _p("string", "Search keyword")}, ["query"]),
    _tool("draft_email", "Draft an email in Outlook.",
          {"to": _p("string", "Recipient email"), "subject": _p("string", "Email subject"), "prompt": _p("string", "Email body content")}, ["prompt"]),
    _tool("generate_content", "Draft longer written content (email, essay, notes, code) to display in the UI panel.",
          {"type": _p("string", "Content type"), "topic": _p("string", "Topic"), "context": _p("string", "Additional context")}, ["topic"]),
    _tool("insert_content", "Paste generated content into the active window.", {"content": _p("string", "Text content to insert")}, ["content"]),
    _tool("type_text", "Type text into an application.",
          {"text": _p("string", "Text to type"), "app": _p("string", "Target app name")}, ["text"]),
    _tool("press_key", "Send a keyboard key or shortcut combination.", {"keys": _p("string", "Key or shortcut, e.g. ctrl+c")}, ["keys"]),
    _tool("click_screen", "Click mouse at specific screen coordinates.", {"x": _p("integer"), "y": _p("integer")}),
    _tool("confirm_action", "Confirm and execute a pending action when the user agrees."),
    _tool("cancel_action", "Cancel a pending action when the user declines or cancels."),
    _tool("stop", "Stop active speech, playback, or running operations."),
    _tool("exit", "Close and terminate the Amigo application."),
]

# Build a set of valid tool names from TOOL_DEFINITIONS for validation
VALID_TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS} | {"play_media", "resume_playback", "get_stock_quote", "market_quote", "stock_price"}


_TOOLS_BLOCK_CACHE: str | None = None


def _tools_block() -> str:
    """Render the tool list dynamically from TOOL_DEFINITIONS."""
    global _TOOLS_BLOCK_CACHE
    if _TOOLS_BLOCK_CACHE is None:
        rows = []
        for tool in TOOL_DEFINITIONS:
            params = tool.get("parameters", {}) or {}
            props = params.get("properties", {}) or {}
            required = set(params.get("required", []) or [])
            if props:
                parts = []
                for key, spec in props.items():
                    part = f"{key}: {spec.get('type', 'string')}{' (required)' if key in required else ''}"
                    if spec.get("description"):
                        part += f" - {spec['description']}"
                    parts.append(part)
                signature = "; ".join(parts)
            else:
                signature = "no params"
            rows.append(f"- {tool['name']}: {tool['description']} [{signature}]")
        _TOOLS_BLOCK_CACHE = "\n".join(rows)
    return _TOOLS_BLOCK_CACHE


def _format_recent_activity(conversation_history: list | None, current_query: str = "", limit: int = 8) -> str:
    """Format recent turns with user queries, tools used, and assistant responses."""
    if conversation_history:
        turns = [t for t in conversation_history if isinstance(t, dict)]
    else:
        try:
            turns = [t for t in (rag_engine.get_recent_conversations(limit) or []) if isinstance(t, dict)]
        except Exception:
            turns = []
    turns = turns[-limit:]

    cur = (current_query or "").strip().lower()
    if turns and cur and (turns[-1].get("user") or turns[-1].get("query") or "").strip().lower() == cur and not (turns[-1].get("assistant") or turns[-1].get("response") or "").strip():
        turns = turns[:-1]

    lines = []
    for turn in turns:
        user_text = (turn.get("user") or turn.get("query") or "").strip()
        if not user_text:
            continue
        lines.append(f"User: {user_text[:250]}")
        tool = (turn.get("tool") or "").strip()
        params = turn.get("params") if isinstance(turn.get("params"), dict) else {}
        reply = (turn.get("assistant") or turn.get("response") or "").strip()
        if tool or reply:
            entry = "Amigo"
            if tool and tool != "chat":
                entry += f" used {tool}"
                if params:
                    clean_p = {k: v for k, v in params.items() if v}
                    if clean_p:
                        entry += f" {json.dumps(clean_p, ensure_ascii=False)[:150]}"
            if reply:
                clean_reply = reply[:200].replace("\n", " ").strip()
                entry += f" and replied: {clean_reply}" if (tool and tool != "chat") else f" replied: {clean_reply}"
            lines.append(entry)
    return "\n".join(lines) if lines else "No recent conversation."


_ROUTER_SYSTEM_PROMPT = """You are the intent router for Amigo, an intelligent Windows desktop voice assistant.
Your task is to understand the user's intent from their latest message, using the conversation context and PC state, and select the appropriate tool action(s).

TOOLS AVAILABLE:
<<TOOLS>>

ROUTING GUIDELINES:
1. INTENT MATCHING:
   - Select tools that directly match the user's explicit or implicit intent based on tool descriptions.
   - Use 'chat' for conversation, greetings, general knowledge, explanations, discussions, advice, questions about previous replies, or when no tool is needed.
   - Use specific tools when the user requests an action on the PC (opening/closing apps, media control, setting reminders/timers, file management) or needs external/current information (web search, live weather, reading screen).
   - If the user asks for multiple things in one turn (e.g. "open notepad and search for python"), return multiple tool actions in the "actions" array in execution order.

2. CONTEXT & FOLLOW-UP RESOLUTION:
   - When the user asks a follow-up question or uses anaphoric references (pronouns like "it", "that", "this", "them", "again", "the previous one", or elliptical continuations like "and tomorrow?", "how about the second one?"), resolve the target entity from RECENT ACTIVITY and ACTIVE STATE.
   - Always supply complete, self-contained parameter values (e.g., the actual song title, search query, application name, or file path) rather than pronouns.
   - If the user is having an ongoing conversation or asking follow-up questions about prior topics (e.g. "why?", "tell me more", "who was that?"), route to 'chat' so the conversational model can answer in full context.

3. MEDIA PLAYBACK vs RESUME:
   - Use 'resume_media' ONLY when the user asks to resume or unpause existing playback without naming a song or artist (e.g. "resume", "unpause", "continue playing").
   - Use 'play_youtube' whenever the user asks to play a song, artist, video, or playlist (e.g. "play believer", "play anything from justin bieber", "play jazz"). Extract the clean song, artist, or music query into the 'query' parameter.

4. CORRECTIONS & CONFIRMATIONS:
   - If the user corrects or refines an earlier request, route based on the corrected intent.
   - If an action was awaiting confirmation: use 'confirm_action' if the user agrees (e.g., 'yes', 'sure', 'go ahead'), or 'cancel_action' if they decline ('no', 'cancel', 'never mind').

5. LIVE MARKET DATA & WEATHER:
   - For live stock prices, share prices, market quotes, or cryptocurrency rates, use 'stock_quote' with the company name, ticker symbol, or crypto name.
   - For weather inquiries or forecasts, use 'get_weather'.

6. LOCAL DOCUMENTS & PERSONAL RECORDS:
   - For questions about the user's tickets, bookings, flights, trains, PNR numbers, invoices, receipts, resumes, or any details from their local files and documents, use 'document_qa'.
   - For finding or searching files by topic or meaning, use 'find_document'.

EXAMPLES:
- "tell me apple stock price" -> {"actions": [{"tool": "stock_quote", "params": {"symbol": "Apple"}}]}
- "how much is bitcoin" -> {"actions": [{"tool": "stock_quote", "params": {"symbol": "Bitcoin"}}]}
- "weather in new york" -> {"actions": [{"tool": "get_weather", "params": {"city": "New York"}}]}
- "play anything from justin bieber" -> {"actions": [{"tool": "play_youtube", "params": {"query": "justin bieber"}}]}
- "what is my pnr number" -> {"actions": [{"tool": "document_qa", "params": {"query": "what is my pnr number"}}]}
- "what does my ticket say" -> {"actions": [{"tool": "document_qa", "params": {"query": "what does my ticket say"}}]}
- "find my resume" -> {"actions": [{"tool": "find_document", "params": {"query": "resume"}}]}

OUTPUT FORMAT:
Respond with exactly one valid JSON object in this format:
{"actions": [{"tool": "<tool_name>", "params": {<parameters>}}]}"""


def build_agent_system_prompt(query: str = "", conversation_history: list | None = None) -> str:
    now = datetime.datetime.now()
    online = is_internet_connected()
    net_status = "Connected (Online)" if online else "Disconnected (Offline)"

    active_context = ""
    try:
        active_context = rag_engine.get_active_context_prompt() or ""
    except Exception:
        pass

    live_media = _get_live_media_title_from_windows()
    if live_media:
        m_title, m_artist, m_status = live_media
        art_str = f" by {m_artist}" if m_artist and m_artist.lower() not in m_title.lower() else ""
        note = f"Active Media: '{m_title}'{art_str} ({m_status.title()})"
        stripped = active_context.rstrip()
        if stripped.endswith("]"):
            active_context = f"{stripped[:-1]} | {note}]"
        else:
            active_context = f"{stripped}\n[Active State: {note}]".strip()
    else:
        try:
            last_song = get_last_played_song(conversation_history)
        except Exception:
            last_song = None
        if last_song and last_song.get("title") and last_song["title"] not in active_context:
            note = f"Last Media: '{last_song['title']}'"
            stripped = active_context.rstrip()
            if stripped.endswith("]"):
                active_context = f"{stripped[:-1]} | {note}]"
            else:
                active_context = f"{stripped}\n[Active State: {note}]".strip()

    parts = [
        _ROUTER_SYSTEM_PROMPT.replace("<<TOOLS>>", _tools_block()),
        "",
        f"Today is {now.strftime('%A, %B')} {now.day}, {now.year}. Local time: {now.strftime('%I:%M %p').lstrip('0')} ({_day_period(now.hour)}). Internet: {net_status}.",
    ]
    pending = _pending_confirmation_line()
    if pending:
        parts.append(pending)
    parts.append("RECENT ACTIVITY (oldest first):")
    parts.append(_format_recent_activity(conversation_history, query))
    if active_context.strip():
        parts.append(active_context.strip())
    try:
        profile = rag_engine.get_user_profile_prompt(query)
        if profile:
            parts.append(profile)
    except Exception:
        pass
    return "\n".join(parts)


_JSON_DECODER = json.JSONDecoder()
_RE_XML_CALL = re.compile(r'<function\s+name="[^"]+">[\s\S]*?</function>')
_ROUTER_STOP = ["<|endoftext|>"]


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
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k != "confirmed"}


def _call_from_obj(obj) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("actions"), list):
        calls: list[dict] = []
        for item in obj["actions"]:
            calls.extend(_call_from_obj(item))
        return calls
    name = obj.get("tool") or obj.get("name")
    if not isinstance(name, str) or not name.strip():
        return []
    speak = obj.get("speak")
    return [{
        "tool": name.strip(),
        "params": _coerce_params(obj["params"] if "params" in obj else obj.get("arguments")),
        "speak": speak if isinstance(speak, str) else "",
    }]


def _parse_xml_calls(text: str) -> list[dict]:
    calls = []
    for match in re.finditer(r'<function\s+name="([^"]+)">([\s\S]*?)</function>', text):
        params = {}
        for pm in re.finditer(r'<param\s+name="([^"]+)">([\s\S]*?)</param>', match.group(2)):
            value = pm.group(2).strip()
            if value.startswith("<![CDATA[") and value.endswith("]]>"):
                value = value[9:-3]
            params[pm.group(1).strip()] = value
        calls.append({"tool": match.group(1).strip(), "params": _coerce_params(params), "speak": ""})
    return calls


def _parse_tool_calls(response: str) -> list[dict]:
    clean = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.I).strip()
    calls: list[dict] = []
    for _start, _end, obj in _iter_json_objects(clean):
        calls.extend(_call_from_obj(obj))
    if not calls:
        calls = _parse_xml_calls(clean)

    valid = []
    for call in calls:
        if call["tool"] in VALID_TOOL_NAMES:
            valid.append(call)
        else:
            logger.warning("[Tool Registry] Ignoring unknown tool %r", call["tool"])
    return valid


def _extract_final_response(response: str, tool_calls: list) -> str:
    clean = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.I).strip()
    parts = []
    pos = 0
    for start, end, obj in _iter_json_objects(clean):
        if _call_from_obj(obj) or (isinstance(obj, dict) and "actions" in obj):
            parts.append(clean[pos:start])
            pos = end
    parts.append(clean[pos:])
    cleaned = _RE_XML_CALL.sub("", "".join(parts))
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned).replace("```", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,\t\n")
    return sanitize_for_tts(cleaned)


def _chat_action(text: str = "") -> dict:
    return {"tool": "chat", "params": {"response": text} if text else {}, "speak": text}


_EXACT_EXIT = frozenset({"goodbye", "close amigo", "exit amigo", "quit amigo"})
_EXACT_STOP = frozenset({"stop", "shut up", "be quiet", "stop talking", "stop speaking", "quiet", "silence"})
_RE_WAKE_WORD = re.compile(r"^(?:(?:hey|hi|hello|ok|okay)\s+)?amigo\b[\s,.:;!-]*", re.IGNORECASE)
_RE_POLITE = re.compile(r"^please\s+|\s+please$", re.IGNORECASE)


def _strip_wake_word(query: str) -> str:
    return _RE_WAKE_WORD.sub("", (query or "").strip()).strip()


def clean_spoken_query(query: str) -> str:
    q = _RE_POLITE.sub("", _strip_wake_word(query))
    return q.strip().rstrip(".!?,;: ").lower()


def parse_user_intent_fast(query: str) -> dict[str, Any] | None:
    """Tier 0: immediate stop / exit shortcuts."""
    if not query or not query.strip():
        return {"tool": "chat", "params": {}, "speak": ""}
    text = clean_spoken_query(query)
    if text in _EXACT_EXIT:
        return {"tool": "exit", "params": {}, "speak": "Goodbye!"}
    if text in _EXACT_STOP:
        return {"tool": "stop", "params": {}, "speak": "Stopped."}
    # Fast path for confirmation: if user says "yes" and there's a pending confirmation, execute it
    pending = get_pending_confirmation()
    if pending and text in ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "do it", "confirm"):
        return {"tool": "confirm_action", "params": {}, "speak": ""}
    if pending and text in ("no", "nope", "cancel", "dont", "don't", "stop", "abort"):
        return {"tool": "cancel_action", "params": {}, "speak": ""}
    return None


def _route_with_llm(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    """Ask local LLM which tool(s) to run using system prompt and conversation history."""
    messages = [
        {"role": "system", "content": build_agent_system_prompt(query, conversation_history=conversation_history)},
        {"role": "user", "content": query},
    ]
    try:
        response = query_local_llm(
            messages,
            system_prompt="",
            max_tokens=384,
            temperature=0.0,
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
    if tool_calls:
        return tool_calls, final_response

    if response.lstrip().startswith("{"):
        final_response = ""
    return [_chat_action(final_response)], final_response


def get_agent_actions(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    """Primary router entry point. Returns (actions_list, spoken_response)."""
    if not query or not query.strip():
        return [{"tool": "chat", "params": {}, "speak": ""}], ""
    fast = parse_user_intent_fast(query)
    if fast is not None:
        return [fast], fast.get("speak", "")
    return _route_with_llm(_strip_wake_word(query) or query.strip(), conversation_history)


def get_agent_actions_with_response(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    return get_agent_actions(query, conversation_history)


def get_agent_action(query: str, conversation_history: list | None = None) -> list[dict]:
    return get_agent_actions(query, conversation_history)[0]


def get_last_played_song(conversation_history: list | None = None) -> dict | None:
    """Most recently played media from active state, UI server, or play_youtube turns."""
    try:
        active_state = get_active_state(clean_expired=False)
        if isinstance(active_state, dict):
            media = active_state.get("current_media")
            if isinstance(media, dict):
                title = media.get("title") or media.get("query")
                if title and title != "No music playing":
                    return {"title": title, "query": media.get("query") or title, "url": media.get("url", "")}
    except Exception:
        pass

    try:
        from amigo.ui import server as ui_server
        media = getattr(ui_server, "_current_media", None)
        if isinstance(media, dict):
            title = media.get("title")
            if title and title != "No music playing":
                return {"title": title, "query": title, "url": media.get("url", "")}
    except Exception:
        pass

    hist = conversation_history
    if not hist:
        try:
            hist = rag_engine.get_recent_conversations(count=10)
        except Exception:
            hist = []
    for turn in reversed(hist or []):
        if not isinstance(turn, dict) or turn.get("tool") != "play_youtube":
            continue
        params = turn.get("params")
        if not isinstance(params, dict):
            params = {}
        query = str(params.get("query") or "").strip()
        if query:
            return {"title": query, "query": query, "url": ""}
        reply = turn.get("assistant") or turn.get("response") or ""
        m = re.search(r"Playing\s+['\"](.+?)['\"]", reply, re.I) or re.search(r"Playing\s+(.+?)(?:\s+on\s+YouTube|\.|$)", reply, re.I)
        if m and m.group(1).strip():
            t = m.group(1).strip().strip("'\"")
            return {"title": t, "query": t, "url": ""}
        said = turn.get("user") or turn.get("query") or ""
        if said:
            return {"title": said, "query": said, "url": ""}
    return None


TOOL_ACTION_PROMPTS = {
    "play_youtube": "play audio or video on YouTube",
    "web_search": "search Google on the web",
    "chat": "explain or tell you about this",
    "open_app": "open an application on your PC",
    "close_app": "close an application",
    "window_mgmt": "manage open windows",
    "desktop_input": "type or interact with your screen",
    "media_control": "control media playback",
    "current_media": "check what song is currently playing",
    "pause_media": "pause media playback",
    "play_media": "resume media playback",
    "next_track": "skip to the next track",
    "prev_track": "go back to the previous track",
    "set_volume": "adjust the volume",
    "get_time": "check the current time",
    "get_date": "check the current date",
    "get_weather": "check the weather forecast",
    "weather": "check the weather forecast",
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
    "workspace": "check email or calendar",
    "get_calendar": "check your calendar schedule",
    "unread_emails": "check your unread emails",
    "set_timer": "set a timer",
    "set_reminder": "set a reminder",
    "find_file": "find files on your PC",
    "type_text": "type text into an application",
    "press_key": "press a keyboard key or shortcut",
    "click_screen": "click on the screen",
    "window_management": "manage window state",
}


def format_clarification_prompt(tool_a: str, tool_b: str, query: str) -> str:
    """Format a clarification prompt when the agent is unsure between two tools."""
    desc_a = TOOL_ACTION_PROMPTS.get(tool_a, f"use {tool_a.replace('_', ' ')}")
    desc_b = TOOL_ACTION_PROMPTS.get(tool_b, f"use {tool_b.replace('_', ' ')}")
    return f"I'm not completely sure â€” did you want to {desc_a}, or {desc_b}?"


def execute_tool(
    tool: str,
    params: dict | None = None,
    query: str = "",
    spoken: str = "",
    conversation_history: list | None = None,
) -> tuple[str, str | None, dict]:
    """
    Unified entry point to execute any assistant tool.
    Returns: (spoken_text, optional_url, optional_metadata)
    """
    handler = UI_TOOL_HANDLERS.get(tool)
    if handler is None:
        logger.warning("Unknown tool %r - answering as chat", tool)
        handler = _tool_chat
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
            elif len(result) == 2:
                return result[0] or "", result[1], {}
            elif len(result) >= 1:
                return result[0] or "", None, {}
        return result or "", None, {}
    except Exception as e:
        logger.exception("Error executing tool '%s'", tool)
        return "Sorry, I couldn't complete that.", None, {"error": str(e), "tool": tool}
    finally:
        _call_ctx.value = None


# ---------------------------------------------------------------------------
# Action Card Generator for React UI
# ---------------------------------------------------------------------------

def build_action_cards(tool: str, params: dict, result_metadata: dict, url: str | None, response_text: str, prompt: str) -> list[dict]:
    """Generates structured Action Cards ONLY for tools that require interactive visual widgets."""
    cards = []

    # 1. Multi-file Disambiguation / Selection
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

    # 2. Weather Visual Widget
    elif tool == "get_weather":
        city = str(params.get("city") or "").strip()
        try:
            w_data = _cached_weather_data(city)
        except Exception:
            w_data = {}
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

    # 2b. Live Stock / Market Quote Visual Card
    elif tool in ("stock_quote", "market_quote", "stock_price", "get_stock_quote") and result_metadata.get("price") is not None:
        sym = result_metadata.get("symbol", "")
        name = result_metadata.get("name", sym)
        price = result_metadata.get("price", "")
        curr = result_metadata.get("currency", "$")
        cards.append({
            "id": f"act-stock-{sym}",
            "type": "general",
            "title": f"{name} ({sym})",
            "subtitle": response_text,
            "selected": True,
            "badge": f"{curr} {price}",
            "payload": {"tool": "stock_quote", **result_metadata},
        })

    # 3. Live Countdown Timer & Stopwatch Widget (only when a timer/stopwatch tool actually ran)
    elif tool in ("set_timer", "timer", "stopwatch"):
        def _secs(text):
            try:
                return parse_relative_seconds(text) or 0
            except Exception:
                return 0

        is_stopwatch = tool == "stopwatch"
        parsed_secs = _secs(str(params.get("query") or "")) or _secs(prompt) or _secs(str(params.get("duration") or params.get("seconds") or ""))
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

    # 4. Screen Vision Perception Card
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

    # 5. Neural Memory Recall Card
    elif tool == "memory_recall" and result_metadata.get("recalled_facts"):
        facts = result_metadata.get("recalled_facts", [])
        cards.append({
            "id": "act-memory",
            "type": "memory",
            "title": "Recalled from Memory",
            "subtitle": facts[0] if facts else response_text[:120],
            "selected": True,
            "badge": "Memory",
            "payload": {
                "tool": "memory_recall",
                "facts": facts,
            },
        })

    # 6. Document Knowledge QA Card
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
            "payload": {
                "tool": "document_qa",
                "matched_docs": docs,
            },
        })

    # 7. Clarification & Disambiguation Action Cards (User Self-Correction / Choice)
    elif tool == "clarification" or result_metadata.get("requires_clarification") or result_metadata.get("status") == "requires_clarification":
        candidates = result_metadata.get("candidates") or params.get("candidate_tools") or ["chat", "web_search"]
        query_text = result_metadata.get("query") or params.get("query") or prompt

        tool_labels = {
            "play_youtube": ("Play on YouTube", "Play audio or video stream"),
            "web_search": ("Search Google Web", "Look up info on the internet"),
            "chat": ("Explain / Answer Questions", "Get AI explanation and chat"),
            "open_app": ("Open Application", "Launch desktop application"),
            "close_app": ("Close Application", "Exit or terminate program"),
            "window_mgmt": ("Manage Windows", "Minimize, maximize, or switch"),
            "weather": ("Weather Forecast", "Check current weather"),
            "get_weather": ("Weather Forecast", "Check current weather"),
            "time_date": ("Time & Date", "Check current time or date"),
            "get_time": ("Current Time", "Check the system clock"),
            "get_date": ("Current Date", "Check today's date"),
            "system_control": ("System Control", "Perform PC action"),
            "screen_vision": ("Inspect Screen", "Analyze screen view"),
            "memory_recall": ("Check Memory", "Recall saved facts"),
            "document_qa": ("Search Documents", "Ask about local files"),
            "find_file": ("Find Files", "Locate files or folders on PC"),
            "current_media": ("Current Song", "Tell which song is currently playing"),
            "pause_media": ("Pause Media", "Pause audio or video playback"),
            "play_media": ("Resume Playback", "Resume playing media"),
            "next_track": ("Next Track", "Skip to next music track"),
            "prev_track": ("Previous Track", "Go back to previous track"),
            "get_calendar": ("Calendar Schedule", "Check upcoming calendar events"),
            "unread_emails": ("Unread Emails", "Check unread Outlook emails"),
        }

        for idx, cand in enumerate(candidates):
            title, desc = tool_labels.get(cand, (cand.replace("_", " ").title(), f"Execute {cand.replace('_', ' ')}"))
            cards.append({
                "id": f"act-clarify-{idx}",
                "type": "general",
                "title": title,
                "subtitle": f"{desc} for '{query_text}'",
                "selected": idx == 0,
                "badge": cand.replace("_", " ").title(),
                "actionType": "button",
                "payload": {"tool": cand, "query": query_text, "original_prompt": prompt},
            })

        if "web_search" not in candidates:
            cards.append({
                "id": "act-clarify-web",
                "type": "general",
                "title": "Search Google Web",
                "subtitle": f"Search online for '{query_text}'",
                "selected": False,
                "badge": "Web Search",
                "actionType": "button",
                "payload": {"tool": "web_search", "query": query_text, "original_prompt": prompt},
            })

    return cards




