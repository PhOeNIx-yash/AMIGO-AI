"""
Tool Registry & Action Dispatcher for Amigo Voice Assistant.
Executes agentic tool calls and generates structured UI action cards.
"""

import datetime
import logging
import os
import re
import urllib.parse
import webbrowser

from ai import get_ai_response, update_active_state, get_active_state
from app_opener import find_files, open_folder, open_windows_app, execute_file_action
from Calculatenumbers import Calc
import os_automation
import rag_engine
from reminder_timer import (
    handle_set_timer,
    handle_set_reminder,
    handle_list_reminders,
    handle_cancel_reminder,
    parse_relative_seconds,
)
from Searchnow import searchGoogle, searchYoutube, scrape_web_info, resolve_youtube_video, clean_search_query
from settings_resolver import open_setting
from weather import weather_command, get_weather_data
from network_utils import is_internet_connected

# Pre-compiled regex patterns for fast matching
_RE_URLS = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]*[^\s<>"{}|\\^`\[\].,;:!?]')
_RE_APP_STRIP = re.compile(r"^(?:please\s+)?(?:open|launch|start|run|show)\s+(?:the\s+|my\s+|an?\s+)?", re.IGNORECASE)
_RE_APP_ARTICLE = re.compile(r"^(?:the|that|my|an?)\s+", re.IGNORECASE)
_RE_MEDIA_CLEAN_TITLE = re.compile(r"^(?:play|playing)\s*:\s*", re.IGNORECASE)
_RE_DOC_STRIP_ACTION = re.compile(r"^(?:please\s+)?(?:can you\s+|can u\s+|could you\s+|could u\s+|will you\s+|would you\s+)?(?:tell me\s+|give me\s+|show me\s+)?(?:open|show|read|summarize|tell me about|what is in|what does|find|locate|check|view|inspect)\s+", re.IGNORECASE)
_RE_DOC_STOPWORDS = re.compile(r"\b(?:the|that|those|these|my|a|an|file|files|document|documents|doc|pdf)\b", re.IGNORECASE)
_RE_DOC_ORDINAL = re.compile(r"\b(?:number\s+(\d+)|(\d+)(?:st|nd|rd|th)?|first|second|third|fourth|fifth)\b", re.IGNORECASE)
_RE_TIMER_PROMPT = re.compile(r"\b(timer|countdown|stopwatch)\b", re.IGNORECASE)

logger = logging.getLogger("amigo.tool_registry")

# Callback to broadcast media state updates to UI
_media_update_cb = None


def set_media_update_callback(cb):
    """Set callback to broadcast media updates to UI."""
    global _media_update_cb
    _media_update_cb = cb


def extract_and_open_urls(text: str) -> bool:
    """Finds URLs in text and opens top matches in default browser."""
    urls = _RE_URLS.findall(text)
    opened = False
    for url in urls[:2]:
        webbrowser.open(url)
        opened = True
    return opened


# ---------------------------------------------------------------------------
# Individual Tool Handlers
# ---------------------------------------------------------------------------

def _tool_web_search(params, query, spoken):
    if not is_internet_connected():
        response = get_ai_response(query, web_context="Error: The PC is currently offline with no internet connection. Explain to the user that you cannot search the web right now because you are not connected to the internet.")
        return response, None, {"status": "offline", "error": "No internet connection"}

    q = params.get("query", query).strip() or query
    clean_q = clean_search_query(q)
    target = clean_q or q

    try:
        update_active_state("last_search", {"query": target})
    except Exception:
        pass

    snippets = scrape_web_info(target)
    search_url = "https://www.google.com/search?q=" + urllib.parse.quote(target)

    if snippets:
        response = get_ai_response(query, web_context=snippets)
        if any(kw in query.lower() for kw in ("browser", "open google", "search google", "show in browser", "open browser")):
            searchGoogle(target)
        return response, search_url

    if any(kw in query.lower() for kw in ("browser", "open google", "search google", "show in browser", "open browser")):
        searchGoogle(target)
    response = get_ai_response(query)
    return response, search_url


def _tool_open_website(params, query, spoken):
    if not is_internet_connected():
        response = get_ai_response(query, web_context="Error: The PC is currently offline with no internet connection. Explain to the user that websites cannot be opened or loaded without an internet connection.")
        return response, None, {"status": "offline", "error": "No internet connection"}
    raw_url = params.get("url", "")
    if raw_url:
        url = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
        webbrowser.open(url)
        try:
            update_active_state("active_subject", {"name": url, "category": "website"})
        except Exception:
            pass
        return spoken, url
    searchGoogle(query)
    return spoken, None


def _tool_play_youtube(params, query, spoken):
    if not is_internet_connected():
        response = get_ai_response(query, web_context="Error: The PC is currently offline with no internet connection. Explain to the user that you cannot search or stream YouTube videos without an internet connection.")
        return response, None, {"status": "offline", "error": "No internet connection"}
    q = params.get("query", query) or query
    media_info = resolve_youtube_video(q)
    clean_q = media_info.get("query") or q

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
    try:
        state = get_active_state(clean_expired=False)
        media_state = state.get("current_media") or {}
        title = media_state.get("title") or media_state.get("query")
        artist = media_state.get("artist") or ""
        if title:
            clean_title = _RE_MEDIA_CLEAN_TITLE.sub("", title).strip()
            by_artist = f" by {artist}" if artist and artist not in ("YouTube Music", "YouTube", "") else ""
            return f"Currently playing '{clean_title}'{by_artist} on YouTube.", media_state.get("url")
    except Exception:
        pass
    return "No song or video is currently playing. Would you like me to play something?", None


def _tool_get_time(params, query, spoken):
    return f"It is currently {datetime.datetime.now().strftime('%I:%M %p').lstrip('0')}.", None


def _tool_get_date(params, query, spoken):
    return f"Today is {datetime.datetime.now().strftime('%A, %B %d, %Y')}.", None


def _tool_get_weather(params, query, spoken):
    if not is_internet_connected():
        response = get_ai_response(query, web_context="Error: The PC is currently offline with no internet connection. Explain to the user that live weather reports cannot be fetched without an internet connection.")
        return response, None, {"status": "offline", "error": "No internet connection"}
    city = params.get("city", "").strip()
    return weather_command(city if city else query) or spoken, None


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
        return f"Opening {clean_target.title()}.", None

    # 2. Is it an already running application?
    if is_app_already_running(clean_target):
        try:
            update_active_state("active_app", {"name": clean_target})
        except Exception:
            pass
        return f"{clean_target.title()} is already open.", None

    # App not found — return clear status without aggressive disambiguation modal lockup
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
        from screen_vision import capture_screen_image
        capture_screen_image("amigo_screenshot.png")
        return "Screenshot saved.", None
    except Exception as e:
        logger.error(f"[Screenshot] Error: {e}")
        return spoken or "Screenshot captured.", None


def _tool_read_screen(params, query, spoken):
    q = params.get("question", query) if isinstance(params, dict) else query
    try:
        from screen_vision import inspect_screen
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


def _tool_memory_recall(params, query, spoken):
    q = params.get("query", query) if isinstance(params, dict) else query
    q_low = (q or "").lower().strip()

    # 1. Storing / Remembering a new personal fact
    if any(q_low.startswith(p) for p in ("remember that", "remember this", "save that", "note that", "store that", "keep in mind")):
        fact = re.sub(
            r"^(?:please\s+)?(?:remember\s+(?:that|this)?|save\s+(?:that|this)?|note\s+(?:that|this)?|store\s+(?:that|this)?|keep\s+in\s+mind\s+(?:that)?)\s*",
            "",
            q,
            flags=re.I,
        ).strip()
        if fact:
            try:
                rag_engine.add_user_fact(fact, category="user_memory")
                return f"I've remembered that: {fact}.", None
            except Exception as e:
                return f"Failed to save fact: {e}", None

    # 2. Recalling a stored fact or past conversation
    recalled_facts = []
    try:
        recalled = rag_engine.search(q, target_collections=[rag_engine.USER_FACTS, rag_engine.CONVERSATIONS], top_k=4)
        for r in recalled:
            if r.get("score", 0) > 0.05 and r.get("text"):
                recalled_facts.append(r["text"])
    except Exception as e:
        logger.debug(f"[Memory Recall]: {e}")

    try:
        prof = rag_engine.load_profile()
        prefs = prof.get("preferences", {})
        for k, v in prefs.items():
            if str(k).lower() in q_low or any(w in str(v).lower() for w in q_low.split()):
                recalled_facts.append(f"{k}: {v}")
    except Exception:
        pass

    if recalled_facts:
        mem_context = "Remembered User Facts & Conversations:\n" + "\n".join(f"- {f}" for f in recalled_facts)
        prompt = f"The user is asking: '{q}'\nUse the remembered information above to answer directly, naturally, and factually."
        response = get_ai_response(prompt, doc_context=mem_context)
        return response, None, {"recalled_facts": recalled_facts[:3]}

    return get_ai_response(q), None


def _tool_document_qa(params, query, spoken):
    q = params.get("query", query) if isinstance(params, dict) else query
    try:
        doc_results = rag_engine.search_documents(q, top_k=5)
        if doc_results:
            doc_context = "\n---\n".join(d["text"] for d in doc_results if d.get("text"))
            prompt = f"The user is asking about their local documents: '{q}'\nAnswer accurately using the document context above."
            response = get_ai_response(prompt, doc_context=doc_context)
            return response, None, {"matched_docs": [d.get("metadata", {}).get("source", "doc") for d in doc_results[:3]]}
    except Exception as e:
        logger.debug(f"[Document QA]: {e}")

    return _tool_ask_document(params, q, spoken)


def _tool_type_text(params, query, spoken):
    app = params.get("app", "").strip() if isinstance(params, dict) else ""
    if app:
        open_windows_app(app)
        import time
        time.sleep(0.25)
    text = params.get("text", "").strip() if isinstance(params, dict) else ""
    if text:
        os_automation.type_text(text)
    return spoken or (f"Typed text into {app}." if app else f"Typed '{text}'." if text else "Typed text."), None


def _tool_press_key(params, query, spoken):
    keys = params.get("keys", "") if isinstance(params, dict) else ""
    if keys:
        os_automation.press_shortcut(keys)
    return spoken or (f"Pressed {keys}." if keys else "Done."), None


def _tool_click_screen(params, query, spoken):
    import pyautogui
    x = params.get("x") if isinstance(params, dict) else None
    y = params.get("y") if isinstance(params, dict) else None
    if x is not None and y is not None:
        try:
            pyautogui.click(int(x), int(y))
            return spoken or f"Clicked at ({x}, {y}).", None
        except Exception as e:
            return f"Click error: {e}", None
    try:
        pyautogui.click()
        return spoken or "Clicked.", None
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


def _tool_stop(params, query, spoken):
    """General stop handler: halts active speech and pauses background playback."""
    try:
        from tts import stop_speaking
        stop_speaking()
    except Exception:
        pass
    if _is_system_audio_playing():
        try:
            os_automation.play_pause_media()
        except Exception:
            pass
    return "Stopped.", None


def _tool_pause_media(params, query, spoken):
    try:
        from tts import stop_speaking
        stop_speaking()
    except Exception:
        pass

    state = get_active_state(clean_expired=True)
    media = state.get("current_media") if isinstance(state, dict) else None
    has_tracked_media = bool(media and isinstance(media, dict))

    try:
        os_automation.play_pause_media()
    except Exception:
        pass

    if _media_update_cb:
        _media_update_cb({"status": "paused"})
    if has_tracked_media and isinstance(media, dict):
        update_active_state("current_media", {**media, "status": "paused"})
    return spoken or "Media paused.", None



def _tool_play_media(params, query, spoken):
    state = get_active_state(clean_expired=False)
    media = state.get("current_media") if isinstance(state, dict) else None
    has_tracked_media = bool(media and isinstance(media, dict))

    try:
        os_automation.play_pause_media()
    except Exception:
        pass

    if _media_update_cb:
        _media_update_cb({"status": "playing"})
    if has_tracked_media and isinstance(media, dict):
        update_active_state("current_media", {**media, "status": "playing"})
    return spoken or "Media resumed.", None


def _tool_next_track(params, query, spoken):
    os_automation.next_track()
    return spoken or "Next track.", None


def _tool_prev_track(params, query, spoken):
    os_automation.prev_track()
    return spoken or "Previous track.", None


def _tool_close_app(params, query, spoken):
    name = params.get("app_name") or params.get("name") or params.get("app") or ""
    closed = os_automation.close_app(name)
    if not name or name.lower() in ("current", "active", "this", "window", "app", "application", "it"):
        return spoken or "Window closed.", None
    if closed:
        return spoken or f"Closed {name.title()}.", None
    return f"{name.title()} is not currently running.", None


def _tool_window_management(params, query, spoken):
    action = params.get("action", "")
    app_name = params.get("app_name", "") or params.get("name", "")
    if action in ("close_window", "close_app") or app_name:
        return _tool_close_app({"app_name": app_name}, query, spoken)
    elif action:
        os_automation.window_action(action)
    return spoken or "Done.", None


def _tool_calculate(params, query, spoken):
    expr = params.get("expression", query).strip()
    if expr:
        res = Calc(expr)
        if res:
            return f"The answer is {res}.", None
    return spoken or "Calculation completed.", None


def _tool_clarification(params, query, spoken):
    """Handles clarifying questions when intent or parameters are ambiguous."""
    candidates = params.get("candidate_tools", []) if isinstance(params, dict) else []
    q = params.get("query", query) if isinstance(params, dict) else query
    msg = spoken or f"I'm not completely sure what you'd like to do with '{q}'. Please choose an option below or clarify."
    return msg, None, {
        "status": "requires_clarification",
        "requires_clarification": True,
        "candidates": candidates,
        "query": q,
    }


def _tool_chat(params, query, spoken):
    # Check if local indexed documents or remembered facts contain relevant knowledge
    rag_ctx = rag_engine.build_rag_context(query, top_k=5)
    if rag_ctx:
        logger.info("[Tool Chat] Local RAG context found for query '%s'", query[:40])
        response = get_ai_response(query, doc_context=rag_ctx)
    else:
        response = get_ai_response(query)

    uncertainty_patterns = (
        "would you like me to look into",
        "would you like me to search",
        "i don't have information",
        "i do not have information",
        "i don't have any information",
        "i do not have any information",
        "can't provide information",
        "cannot provide information",
        "can't find information",
        "cannot find information",
        "unable to provide information",
        "unable to find information",
        "no information available",
        "no information about",
        "don't have details",
        "do not have details",
        "i don't know much about",
        "i do not know much about",
        "i am not familiar with",
        "i'm not familiar with",
        "i am not sure",
        "i'm not sure",
        "i don't know",
        "i do not know",
        "i don't have access to real-time",
        "i do not have access to real-time",
        "i don't have access",
        "i do not have access",
        "as an ai, i don't have",
        "my knowledge is limited",
        "sorry, i can't provide",
        "sorry, i cannot provide",
    )
    is_uncertain = any(p in response.lower() for p in uncertainty_patterns)

    # If the model expressed uncertainty or lack of information, autonomously search the web
    if is_uncertain and is_internet_connected():
        logger.info("[Tool Chat] Model expressed uncertainty about '%s'. Autonomously fetching web knowledge...", query[:40])
        clean_q = clean_search_query(query)
        snippets = scrape_web_info(clean_q or query)
        if snippets:
            response = get_ai_response(query, web_context=snippets)
            extract_and_open_urls(response)
            return response, None

    extract_and_open_urls(response)
    return response, None





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
    "empty_recycle_bin":  (os_automation.empty_recycle_bin, "Recycle bin emptied."),
    "cancel_shutdown":    (os_automation.cancel_shutdown, "Shutdown cancelled."),
}


def _make_simple_handler(func, default_msg):
    def _handler(params, query, spoken):
        func()
        return spoken or default_msg, None
    return _handler


# ---------------------------------------------------------------------------
# Dynamic Document Resolver & File Actions
# ---------------------------------------------------------------------------

def resolve_document_path(query_or_target: str) -> str | None:
    """
    Dynamically resolves a local file path from a user query or filename.
    1. Checks direct filesystem path.
    2. Resolves pronoun/follow-up references ('that', 'it', 'this', 'the file') from active memory state.
    3. Searches local filesystem by name.
    4. Searches ChromaDB vector store by semantic meaning.
    """
    state = get_active_state(clean_expired=False)
    active_f = state.get("active_file")
    active_path = active_f.get("path") if isinstance(active_f, dict) else None

    if not query_or_target or not isinstance(query_or_target, str):
        return active_path if (active_path and os.path.exists(active_path)) else None

    raw = query_or_target.strip()
    if os.path.exists(raw) and os.path.isfile(raw):
        return raw

    # Check for pronoun / follow-up references ('open that', 'open it', 'open this', 'open those', 'open the file')
    clean = _RE_DOC_STRIP_ACTION.sub("", raw)
    clean = _RE_DOC_STOPWORDS.sub("", clean).strip()

    # Check for ordinal / numbered references ("open number 1", "open number 2", "open first", "open second", "open 3rd")
    found_list = state.get("found_files", [])
    if isinstance(found_list, list) and found_list:
        if m := _RE_DOC_ORDINAL.search(raw):
            matched_text = m.group(0).lower()
            idx = 0
            if "1" in matched_text or "first" in matched_text:
                idx = 0
            elif "2" in matched_text or "second" in matched_text:
                idx = 1
            elif "3" in matched_text or "third" in matched_text:
                idx = 2
            elif "4" in matched_text or "fourth" in matched_text:
                idx = 3
            elif "5" in matched_text or "fifth" in matched_text:
                idx = 4
            if idx < len(found_list) and os.path.exists(found_list[idx]):
                return found_list[idx]

    # If the user is referring to a previous file ("that", "it", "those", "the file", "the first one", etc.)
    if not clean or clean.lower() in ("that", "it", "this", "those", "them", "these", "first", "one", "the first one", "selected", "file", "files", "document", "documents"):
        if active_path and os.path.exists(active_path):
            return active_path
        return None


    search_terms = [clean, raw] if clean and clean != raw else [raw]
    m_doc = re.search(r"\b(?:from|in|of|about)\s+([a-zA-Z0-9_\-\.\s]{2,40})", raw, re.I)
    if m_doc:
        candidate = m_doc.group(1).strip()
        candidate = _RE_DOC_STOPWORDS.sub("", candidate).strip()
        if candidate and candidate not in search_terms:
            search_terms.insert(0, candidate)

    # 1. Filename lookup
    for term in search_terms:
        if len(term) >= 2:
            matches, _ = find_files(term)
            if matches:
                update_active_state("active_file", {"path": matches[0], "name": os.path.basename(matches[0])})
                return matches[0]

    # 2. Semantic vector lookup in ChromaDB (only for real meaningful terms, never stopwords/pronouns!)
    for term in search_terms:
        if len(term) >= 3 and term.lower() not in ("that", "this", "those", "them", "file", "files", "document", "documents", "open", "show"):
            results = rag_engine.search_files_by_context(term, top_k=1)
            if results and results[0].get("score", 0) > 0.30:
                fpath = results[0].get("filepath")
                if fpath:
                    update_active_state("active_file", {"path": fpath, "name": os.path.basename(fpath)})
                    return fpath

    # 3. Contextual fallback: most recently referenced active file
    if active_path and os.path.exists(active_path):
        return active_path

    return None



def _tool_file_action(params, query, spoken):
    """Open or execute an action on a local document."""
    target = (params.get("path") or params.get("name") or query) if isinstance(params, dict) else query
    action = params.get("action", "open") if isinstance(params, dict) else "open"
    target_path = resolve_document_path(str(target))

    if target_path and os.path.exists(target_path):
        try:
            update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        except Exception:
            pass
        ok, msg = execute_file_action(target_path, action)
        clean_name = os.path.splitext(os.path.basename(target_path))[0].replace("_", " ").replace("-", " ").title()
        return f"Opening {clean_name}.", None
    return "I couldn't find that file on your computer.", None



def _tool_show_images(params, query, spoken):
    return spoken or "Showing images.", "https://www.google.com/search?q=" + urllib.parse.quote(params.get("query", query))

def _tool_search_and_type(params, query, spoken):
    os_automation.search_and_type(params.get("text", ""))
    return spoken, None

def _tool_new_tab(params, query, spoken):
    os_automation.new_tab(params.get("url", ""))
    return spoken, None


def _tool_set_volume(params, query, spoken):
    res = os_automation.set_volume(params.get("level", "50"))
    if isinstance(res, (tuple, list)):
        _, msg = res
    elif isinstance(res, str):
        msg = res
    else:
        msg = f"Volume set to {params.get('level', '50')} percent."
    return msg, None

def _tool_set_brightness(params, query, spoken):
    res = os_automation.set_brightness(params.get("level", "50"))
    if isinstance(res, (tuple, list)):
        _, msg = res
    elif isinstance(res, str):
        msg = res
    else:
        msg = f"Brightness set to {params.get('level', '50')} percent."
    return msg, None

def _tool_open_settings(params, query, spoken):
    return open_setting(params.get("setting", "")), None

def _tool_system_status(params, query, spoken):
    return os_automation.get_system_status().get("summary", "System status checked."), None

def _tool_restart_pc(params, query, spoken):
    os_automation.restart_pc(30)
    return spoken or "Restarting in 30 seconds.", None

def _tool_set_timer(params, query, spoken):
    return handle_set_timer(params, query), None

def _tool_set_reminder(params, query, spoken):
    return handle_set_reminder(params, query), None

def _tool_list_reminders(params, query, spoken):
    return handle_list_reminders(), None

def _tool_cancel_reminder(params, query, spoken):
    return handle_cancel_reminder(params), None

def _tool_exit(params, query, spoken):
    return spoken or "Goodbye!", None


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
    "calculate":         _tool_calculate,
    "set_volume":        _tool_set_volume,
    "set_brightness":    _tool_set_brightness,
    "open_settings":     _tool_open_settings,
    "system_status":     _tool_system_status,
    "hardware_metrics":  _tool_system_status,
    "restart_pc":        _tool_restart_pc,
    "pause_media":       _tool_pause_media,
    "play_media":        _tool_play_media,
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
    "ask_document":      _tool_document_qa,
    "current_media":     _tool_get_current_media,
    "get_current_media": _tool_get_current_media,
    "clarification":     _tool_clarification,
    "time_date":         _tool_get_time,
    "exit":              _tool_exit,
    "chat":              _tool_chat,
}


# ---------------------------------------------------------------------------
# Dynamic Document Resolver & RAG Handlers
# ---------------------------------------------------------------------------


def _tool_ask_document(params, query, spoken):
    """RAG Q&A on documents or knowledge base."""
    filepath = params.get("filepath", "") if isinstance(params, dict) else ""
    question = (params.get("question", query) if isinstance(params, dict) else query).strip() or query

    target_path = resolve_document_path(filepath) or resolve_document_path(question) or resolve_document_path(query)
    if target_path:
        try:
            update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        except Exception:
            pass
        ctx = rag_engine.build_file_context(target_path, question)
    else:
        ctx = rag_engine.build_rag_context(question, top_k=6)

    target_prompt = query if (query and any(k in query.lower() for k in ("synopsis", "summary", "summarize", "overview", "detail", "explain", "pan", "pin", "what", "how", "who"))) else question
    if ctx:
        return get_ai_response(target_prompt, doc_context=ctx), None
    return get_ai_response(target_prompt), None



def _tool_summarize_document(params, query, spoken):
    """Summarize a document using RAG + LLM."""
    filepath = params.get("filepath", "") if isinstance(params, dict) else ""
    target_path = resolve_document_path(filepath) or resolve_document_path(query)

    if target_path:
        try:
            update_active_state("active_file", {"path": target_path, "name": os.path.basename(target_path)})
        except Exception:
            pass
        ctx = rag_engine.get_file_summary_context(target_path)
    else:
        ctx = rag_engine.build_rag_context(query, top_k=6)

    if ctx:
        target_prompt = query if query and any(k in query.lower() for k in ("synopsis", "summary", "summarize", "overview", "brief")) else f"Summarize this document concisely:\n{query}"
        return get_ai_response(target_prompt, doc_context=ctx), None
    return "I couldn't find that document to summarize.", None


def _tool_find_document(params, query, spoken):
    """Semantic file discovery — find files by meaning, not just name."""
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
        from mail_integration import get_email_summary_text, is_outlook_available
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
        from mail_integration import search_emails, is_outlook_available
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
        from mail_integration import get_unread_count, get_unread_emails, is_outlook_available
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
        from mail_integration import draft_email
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
        from calendar_integration import get_calendar_summary_text, get_upcoming_summary_text, is_outlook_available
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
        from calendar_integration import search_events, is_outlook_available
        if not is_outlook_available():
            return "Outlook Calendar is not available.", None
        q = params.get("query", query).strip()
        results = search_events(q, days=30)
        if results:
            lines = [f"Found {len(results)} event{'s' if len(results)>1 else ''} matching '{q}':"]
            for i, e in enumerate(results, 1):
                loc = f" at {e['location']}" if e.get('location') else ""
                lines.append(f"  {i}. {e['subject']}{loc} — {e['start']}")
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
    UI_TOOL_HANDLERS[_key] = _make_simple_handler(_func, _msg)


def execute_tool(tool: str, params: dict, query: str = "", spoken: str = "") -> tuple[str, str | None, dict]:
    """
    Unified entry point to execute any assistant tool.
    Returns: (spoken_text, optional_url, optional_metadata)
    """
    handler = UI_TOOL_HANDLERS.get(tool, _tool_chat)
    try:
        result = handler(params, query, spoken)
        if isinstance(result, tuple):
            if len(result) == 3:
                return result[0] or "", result[1], result[2]
            return result[0] or "", result[1], {}
        return result or "", None, {}
    except Exception as e:
        logger.error(f"Error executing tool '{tool}': {e}")
        return spoken or f"An error occurred while executing {tool}.", None, {}


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
        city = params.get("city", "").strip()
        w_data = get_weather_data(city) if city else get_weather_data("")
        resolved_city = w_data.get("city", city.title() if city else "Local Area")
        cards.append({
            "id": "act-weather",
            "type": "weather",
            "title": f"Weather in {resolved_city}",
            "subtitle": response_text,
            "selected": True,
            "badge": "Weather",
            "payload": {"tool": "get_weather", "city": resolved_city, **w_data},
        })

    # 3. Live Countdown Timer & Stopwatch Widget
    elif tool in ("set_timer", "timer", "stopwatch") or _RE_TIMER_PROMPT.search(prompt):
        parsed_secs = parse_relative_seconds(prompt) or parse_relative_seconds(str(params.get("duration") or params.get("seconds") or 60)) or 60
        is_stopwatch = tool == "stopwatch" or "stopwatch" in prompt.lower()
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
    elif tool in ("document_qa", "ask_document") and result_metadata.get("matched_docs"):
        docs = result_metadata.get("matched_docs", [])
        first_doc = os.path.basename(docs[0]) if docs else "Local Document"
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
            "time_date": ("Time & Date", "Check current time or date"),
            "system_control": ("System Control", "Perform PC action"),
            "screen_vision": ("Inspect Screen", "Analyze screen view"),
            "memory_recall": ("Check Memory", "Recall saved facts"),
            "document_qa": ("Search Documents", "Ask about local files"),
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


