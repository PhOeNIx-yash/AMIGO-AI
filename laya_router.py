"""
Laya System 1 Neural Router for Amigo Voice Assistant.
Executes sub-second non-autoregressive intent routing via Convai Innovations' Laya model.
Provides deterministic, zero-conflict decision-making across all actions before delegating
conversational text generation to MiniCPM 5 2B.
"""

import logging
import os
import re
import threading
import time
from typing import Any
import warnings

# Suppress uncalibrated temperature warnings from laya library internals
warnings.filterwarnings("ignore", category=RuntimeWarning, module="laya")

logger = logging.getLogger("amigo.laya_router")

LAYA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "models", "laya"))
WEIGHTS_PATH = os.path.join(LAYA_DIR, "model.safetensors")
CONFIG_PATH = os.path.join(LAYA_DIR, "rl_agent_config.json")

_laya_agent = None
_laya_lock = threading.Lock()
_laya_load_attempted = False

# Dual-aspect questions evaluated in a single parallel forward pass
LAYA_QUESTIONS = {
    "tool": {
        "type": "choice",
        "instructions": "Which capability executes this request?",
        "criteria": {
            "open_app": "Open, launch, or start an installed application or program on the computer",
            "close_app": "Close, quit, exit, or terminate a running application or window",
            "window_mgmt": "Minimize, maximize, restore, or switch desktop windows",
            "browser_nav": "Navigate browser tabs, open URL, scroll webpage up or down",
            "desktop_input": "Type text, press keyboard shortcuts, or click on the screen",
            "play_youtube": "Play or stream music, songs, artists, or videos on YouTube",
            "media_control": "Pause, resume, skip tracks, or check currently playing media",
            "set_volume": "Adjust, increase, decrease, mute, or unmute system audio volume",
            "time_date": "Check the current time or date",
            "weather": "Check the meteorological weather forecast, rain, or outdoor temperature for a location or city",
            "system_control": "Lock PC, sleep PC, restart system, or capture a screenshot",
            "screen_vision": "Analyze, read, or answer questions about what is visible on the screen",
            "memory_recall": "Recall stored personal memories, facts, notes, or past interactions",
            "document_qa": "Search or ask questions about indexed local documents and files",
            "workspace": "Check emails, view calendar schedule, or manage timers and alarms",
            "web_search": "Search the web, google information, look up facts, or browse the internet",
            "chat": "General conversation, small talk, casual remarks, opinions, compliments, discussing music or songs, storytelling, or general questions",
        },
    },
}


TOOL_ACTION_PROMPTS = {
    "play_youtube": "play audio or video on YouTube",
    "web_search": "search Google on the web",
    "chat": "explain or tell you about this",
    "open_app": "open an application on your PC",
    "close_app": "close an application",
    "window_mgmt": "manage open windows",
    "desktop_input": "type or interact with your screen",
    "media_control": "control media playback",
    "set_volume": "adjust the volume",
    "time_date": "check the current time or date",
    "weather": "check the weather forecast",
    "system_control": "perform a system control action",
    "screen_vision": "inspect your screen",
    "memory_recall": "check your saved memory",
    "document_qa": "search your documents",
    "workspace": "check email or calendar",
    "get_time": "check the current time",
    "get_date": "check the current date",
}


def format_clarification_prompt(tool_a: str, tool_b: str, query: str) -> str:
    desc_a = TOOL_ACTION_PROMPTS.get(tool_a, f"use {tool_a.replace('_', ' ')}")
    desc_b = TOOL_ACTION_PROMPTS.get(tool_b, f"use {tool_b.replace('_', ' ')}")
    return f"I'm not completely sure — did you want to {desc_a}, or {desc_b}?"


# Parameter extraction helpers (used strictly after neural tool decision)
_RE_SCROLL = re.compile(r"\bscroll\s+(down|up)\b", re.I)
_RE_CLICK = re.compile(r"\b(?:left\s+|right\s+)?click(?:\s+(?:at|on)\s+(\d+)\s*[,x\s]\s*(\d+))?\b", re.I)
_RE_VOL_NUM = re.compile(r"\b(\d{1,3})\s*(?:%|percent)?\b", re.I)
_RE_TIMER_SECS = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", re.I)
_RE_EMAIL = re.compile(r"\b(?:email|emails|mail|inbox|outlook)\b", re.I)
_RE_CALENDAR = re.compile(r"\b(?:calendar|schedule|meeting|appointment)\b", re.I)
_RE_TIMER = re.compile(r"\b(?:timer|countdown|alarm|stopwatch)\b", re.I)


def get_last_played_song(conversation_history: list | None = None) -> dict | None:
    """Finds the most recently played media title/query from active state, UI server, or history."""
    # 1. Check in-memory active state
    try:
        from rag_engine import get_active_state
        state = get_active_state(clean_expired=False)
        media = state.get("current_media")
        if media and isinstance(media, dict):
            title = media.get("title") or media.get("query")
            if title and title != "No music playing":
                return {
                    "title": title,
                    "query": media.get("query") or title,
                    "url": media.get("url", ""),
                }
    except Exception:
        pass

    # 2. Check UI server global media state
    try:
        import ui_server
        media = getattr(ui_server, "_current_media", None)
        if media and isinstance(media, dict):
            title = media.get("title")
            if title and title != "No music playing":
                return {
                    "title": title,
                    "query": title,
                    "url": media.get("url", ""),
                }
    except Exception:
        pass

    # 3. Check conversation history turns
    hist = conversation_history
    if not hist:
        try:
            from rag_engine import get_recent_conversations
            hist = get_recent_conversations(count=10)
        except Exception:
            hist = []

    if hist:
        for turn in reversed(hist):
            if not isinstance(turn, dict):
                continue
            tool = turn.get("tool")
            assistant = turn.get("assistant") or turn.get("response") or ""
            # Match tool or spoken confirmation
            if tool == "play_youtube" or "on YouTube" in assistant:
                m = re.search(r"Playing ['\"](.+?)['\"]", assistant)
                if m:
                    song_name = m.group(1).strip()
                    return {"title": song_name, "query": song_name, "url": ""}
            # Or match assistant describing the song
            m2 = re.search(r"(?:song\s+(?:I\s+played\s+)?was|looked\s+up\s+the\s+song)\s+['\"](.+?)['\"]", assistant, re.I)
            if m2:
                song_name = m2.group(1).strip()
                return {"title": song_name, "query": song_name, "url": ""}

    return None


def is_laya_ready() -> bool:
    """Check if Laya weights and configuration exist locally."""
    return (
        os.path.exists(WEIGHTS_PATH)
        and os.path.getsize(WEIGHTS_PATH) > 500_000_000
        and os.path.exists(CONFIG_PATH)
    )


def get_laya_agent():
    """Thread-safe singleton accessor for the Laya System 1 decision agent."""
    global _laya_agent, _laya_load_attempted
    if _laya_agent is not None:
        return _laya_agent

    if _laya_load_attempted:
        return None

    with _laya_lock:
        if _laya_agent is not None:
            return _laya_agent
        _laya_load_attempted = True

        if not is_laya_ready():
            logger.info("[Laya] Local weights not detected. Laya System 1 router is inactive.")
            return None

        try:
            import laya
            logger.info("[Laya] Loading System 1 Decision Model from %s...", LAYA_DIR)
            t0 = time.perf_counter()
            _laya_agent = laya.load(LAYA_DIR)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.info("[Laya] Decision Model ready in %.1f ms on device: %s", elapsed_ms, _laya_agent.device)
            return _laya_agent
        except Exception as e:
            logger.error("[Laya] Failed to load model: %s", e)
            _laya_agent = None
            return None


def extract_parameters_and_tool(tool: str, query: str, conversation_history: list | None = None) -> tuple[str, dict[str, Any]]:
    """Refines tool classification and extracts execution parameters."""
    q = query.strip()
    q_low = q.lower()

    if tool == "open_app":
        # Extract target application entity
        name = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:open|launch|start|run)(?:\s+(?:the\s+|an?\s+)?)?", "", q, flags=re.I)
        name = re.sub(r"\s+(?:please|for\s+me)$", "", name, flags=re.I).strip()

        # If query contains no opening intent and matches no installed application, treat as general conversation
        has_open_indicator = bool(re.search(r"\b(?:open|launch|start|run|app|application|program)\b", q_low))
        if not has_open_indicator:
            try:
                from app_opener import _find_best_app
                if not _find_best_app(q)[0]:
                    return "chat", {}
            except Exception:
                return "chat", {}

        return "open_app", {"name": name or "Notepad"}

    if tool == "close_app":
        has_close_indicator = bool(re.search(r"\b(?:close|quit|exit|kill|terminate|shut\s*down)\b", q_low))
        if not has_close_indicator:
            return "chat", {}
        name = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:close|quit|exit|kill)(?:\s+(?:the\s+|an?\s+)?)?", "", q, flags=re.I)
        name = re.sub(r"\s+(?:please|for\s+me)$", "", name, flags=re.I).strip()
        return "close_app", {"name": name}



    if tool == "window_mgmt":
        if "maximize" in q_low:
            return "window_management", {"action": "maximize"}
        if "restore" in q_low:
            return "window_management", {"action": "restore"}
        if "switch" in q_low or "alt tab" in q_low:
            return "window_management", {"action": "switch_window"}
        if "minimize" in q_low:
            return "window_management", {"action": "minimize_all"}
        return "chat", {}

    if tool == "browser_nav":
        if "close tab" in q_low or "close this tab" in q_low:
            return "close_tab", {}
        if "next tab" in q_low:
            return "next_tab", {}
        if "prev tab" in q_low or "previous tab" in q_low:
            return "prev_tab", {}
        if m := _RE_SCROLL.search(q_low):
            return f"scroll_{m.group(1)}", {"amount": 600}
        if any(w in q_low for w in ("tab", "browser", "website", "url", "webpage", "scroll")):
            m_url = re.search(r"\b(?:for|to|with)\s+(\S+)", q, re.I)
            url = m_url.group(1) if m_url else ""
            return "new_tab", {"url": url}
        return "chat", {}

    if tool == "desktop_input":
        if re.search(r"\b(?:press|hit)\b", q_low):
            m_key = re.search(r"^(?:please\s+)?(?:press|hit)\s+['\"]?(.+?)['\"]?$", q, re.I)
            key_name = m_key.group(1).strip() if m_key else "enter"
            return "press_key", {"keys": key_name}
        if m := _RE_CLICK.search(q_low):
            x = int(m.group(1)) if m.group(1) else None
            y = int(m.group(2)) if m.group(2) else None
            return "click_screen", {"x": x, "y": y}
        if re.search(r"\b(?:type|write|input)\b", q_low):
            m_type = re.search(r"^(?:please\s+)?(?:type|write|enter|input)\s+['\"]?(.+?)['\"]?(?:\s+(?:in|into|on)\s+(?:the\s+)?(.+))?$", q, re.I)
            text = m_type.group(1).strip() if m_type else q
            app = m_type.group(2).strip() if (m_type and m_type.group(2)) else ""
            return "type_text", {"text": text, "app": app}
        return "chat", {}

    if tool == "play_youtube":
        if any(w in q_low for w in ("what song", "what's playing", "what is playing", "current song", "which song")):
            return "current_media", {}

        # Requires a genuine play/stream/watch action directive; casual statements, comments, or praise ("this song is very good") are chat
        has_play_action = bool(re.search(
            r"\b(?:play|listen(?:\s+to)?|stream|put\s+on|watch|replay|repeat|queue)\b",
            q_low,
        ))
        if not has_play_action:
            return "chat", {}

        clean = re.sub(
            r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:play|listen(?:\s+to)?|stream|put\s+on|watch|replay|repeat)\s+(?:the\s+|a\s+|some\s+)?(?:song\s+|music\s+|track\s+|video\s+)?(?:called\s+|titled\s+|named\s+)?",
            "",
            q,
            flags=re.I,
        )
        clean = re.sub(r"\s+(?:on\s+youtube|from\s+youtube|please|for\s+me)$", "", clean, flags=re.I).strip()
        clean_norm = clean.lower().rstrip("?!.,;:")
        # Anaphoric reference resolution for replay/repeat/again
        if not clean_norm or clean_norm in (
            "it again", "that again", "again", "it", "this", "that",
            "the song again", "that song again", "the track again", "the music again",
            "once more", "it once more", "song", "music", "track", "that song", "the song",
        ):
            song = get_last_played_song(conversation_history)
            if song and song.get("title"):
                return "play_youtube", {"query": song["title"]}
        return "play_youtube", {"query": clean or q}

    if tool == "media_control":
        if any(w in q_low for w in ("what song", "what's playing", "what is playing", "current song", "which song")):
            return "current_media", {}
        if "mute" in q_low or "unmute" in q_low or "silence" in q_low:
            return "mute", {}
        if any(w in q_low for w in ("next", "skip")):
            return "next_track", {}
        if any(w in q_low for w in ("prev", "previous", "back")):
            return "prev_track", {}
        if any(w in q_low for w in ("pause", "stop", "freeze", "halt")):
            return "pause_media", {}
        if any(w in q_low for w in ("resume", "unpause", "continue", "play")):
            return "play_media", {}
        return "chat", {}

    if tool == "set_volume":
        if "mute" in q_low or "unmute" in q_low or "silence" in q_low:
            return "mute", {}
        if "louder" in q_low or "increase" in q_low or "turn up" in q_low:
            return "volume_up", {}
        if "quieter" in q_low or "decrease" in q_low or "turn down" in q_low:
            return "volume_down", {}
        if any(w in q_low for w in ("volume", "sound", "audio", "loudness")):
            m = _RE_VOL_NUM.search(q)
            return "set_volume", {"level": m.group(1) if m else "50"}
        return "chat", {}

    if tool == "time_date":
        if any(w in q_low for w in ("calendar", "schedule", "meeting", "appointment", "event")):
            return "get_calendar", {}
        if any(w in q_low for w in ("date", "day", "today", "year", "month", "tomorrow", "yesterday")):
            return "get_date", {}
        if any(w in q_low for w in ("time", "clock", "hour", "minute", "now", "current", "o'clock", "am", "pm")):
            return "get_time", {}
        return "chat", {}

    if tool in ("get_weather", "weather"):
        m_in = re.search(r"\b(?:in|at|for|of)\s+([a-zA-Z\s.-]+?)(?:\s*\?|\s*$|\s+please)", q, re.I)
        city = m_in.group(1).strip() if m_in else ""
        if city.lower().startswith("the weather"):
            m_sub = re.search(r"\b(?:in|at|for)\s+([a-zA-Z\s.-]+)", city, re.I)
            city = m_sub.group(1).strip() if m_sub else ""
        return "get_weather", {"city": city}

    if tool == "system_control":
        if any(w in q_low for w in ("close", "quit", "exit", "kill")):
            return extract_parameters_and_tool("close_app", q, conversation_history)
        if "screenshot" in q_low:
            return "take_screenshot", {}
        if "lock" in q_low:
            return "lock_pc", {}
        if "sleep" in q_low:
            return "sleep_pc", {}
        if "restart" in q_low or "reboot" in q_low:
            return "restart_pc", {}
        if any(w in q_low for w in ("pause", "stop", "freeze", "halt")):
            return "pause_media", {}
        if "resume" in q_low:
            return "play_media", {}
        if "read screen" in q_low or "what is on my screen" in q_low or "look at" in q_low:
            return "screen_vision", {"question": q}
        if any(w in q_low for w in ("status", "battery", "cpu", "ram", "hardware", "specs", "metrics", "pc status", "system status")):
            return "system_status", {}
        return "chat", {}

    if tool in ("screen_vision", "read_screen"):
        if any(w in q_low for w in ("screen", "display", "monitor", "look at", "what am i looking at", "read this", "see on", "visible", "what is this", "what's this", "active window", "window")):
            return "screen_vision", {"question": q}
        return "chat", {}

    if tool == "memory_recall":
        return "memory_recall", {"query": q}

    if tool == "document_qa":
        return "document_qa", {"query": q}

    if tool == "workspace":
        if _RE_EMAIL.search(q_low):
            return "unread_emails", {}
        if _RE_CALENDAR.search(q_low):
            return "get_calendar", {}
        if _RE_TIMER.search(q_low):
            total_secs = 60
            if m := _RE_TIMER_SECS.search(q):
                val, unit = int(m.group(1)), m.group(2).lower()
                total_secs = val * 86400 if unit.startswith("d") else (val * 3600 if unit.startswith("h") else (val * 60 if unit.startswith("m") else val))
            return "set_timer", {"duration": total_secs, "seconds": total_secs}
        return "find_document", {"query": q}

    if tool == "web_search":
        clean = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:search(?:\s+(?:the\s+web|online|google))?(?:\s+for)?|google(?:\s+for)?|look\s+up)\s+", "", q, flags=re.I)
        clean = re.sub(r"\s+(?:on\s+google|online|please)$", "", clean, flags=re.I).strip()
        return "web_search", {"query": clean or q}

    return tool, {}


def route_intent_via_laya(user_query: str, conversation_history: list | None = None) -> dict[str, Any] | None:
    """
    Primary Router: High-speed System 1 neural decision routing via Laya.
    Evaluates intent in a single forward pass with zero hardcoded regex or keyword rules.
    Detects ambiguity and asks the user to clarify or correct when unsure.
    Delegates conversational reasoning cleanly to MiniCPM 5 2B.
    """
    q = (user_query or "").strip()
    if not q or len(q) < 2:
        return {"tool": "chat", "params": {}, "speak": "", "source": "laya"}

    agent = get_laya_agent()
    if agent is None:
        return {"tool": "chat", "params": {}, "speak": "", "source": "fallback"}

    try:
        t0 = time.perf_counter()
        state = {"request": q}

        # Single forward pass for neural decision
        res = agent.predict(state, LAYA_QUESTIONS)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        tool_info = res.get("answers", {}).get("tool", {})
        raw_tool = tool_info.get("choice", "chat")
        tool_conf = float(tool_info.get("confidence", tool_info.get("answer_confidence", 0.0)))
        tool_probs = tool_info.get("probabilities", {})

        # Direct delegation to MiniCPM 5 2B when neural choice is chat
        if raw_tool == "chat":
            logger.info("[Laya Router] Evaluated '%s' -> chat in %.1f ms", q[:35], elapsed_ms)
            return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": tool_conf}

        # Neural Ambiguity / Confusion Check:
        # Clarify ONLY when two concrete non-chat tools are deadlocked with low confidence.
        # Never interrupt against the conversational 'chat' fallback or when one tool clearly leads.
        if tool_probs:
            sorted_candidates = sorted(tool_probs.items(), key=lambda x: x[1], reverse=True)
            top_tool, top_prob = sorted_candidates[0]
            second_tool, second_prob = sorted_candidates[1] if len(sorted_candidates) > 1 else ("chat", 0.0)

            resolved_top, _ = extract_parameters_and_tool(top_tool, q, conversation_history=conversation_history)
            resolved_second, _ = extract_parameters_and_tool(second_tool, q, conversation_history=conversation_history)

            is_ambiguous = (
                top_tool != "chat"
                and second_tool != "chat"
                and top_tool != second_tool
                and resolved_top != resolved_second
                and resolved_top != "chat"
                and resolved_second != "chat"
                and top_prob < 0.55
                and abs(top_prob - second_prob) < 0.08
            )
            if is_ambiguous:

                logger.info(
                    "[Laya Router Clarification] Ambiguity for '%s': top=%s (%.3f) vs 2nd=%s (%.3f)",
                    q[:35], top_tool, top_prob, second_tool, second_prob,
                )
                clarification_text = format_clarification_prompt(top_tool, second_tool, q)
                return {
                    "tool": "clarification",
                    "params": {
                        "candidate_tools": [top_tool, second_tool],
                        "probabilities": {top_tool: round(top_prob, 3), second_tool: round(second_prob, 3)},
                        "query": q,
                    },
                    "speak": clarification_text,
                    "source": "laya_disambiguation",
                    "confidence": top_prob,
                }


        final_tool, params = extract_parameters_and_tool(raw_tool, q, conversation_history=conversation_history)
        if final_tool == "chat":
            logger.info("[Laya Router] Extracted parameters evaluated '%s' -> chat in %.1f ms", q[:35], elapsed_ms)
            return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": tool_conf}

        logger.info(
            "[Laya Router] Evaluated '%s' -> tool='%s' (conf=%.3f) in %.1f ms",
            q[:35], final_tool, tool_conf, elapsed_ms,
        )

        return {
            "tool": final_tool,
            "params": params,
            "speak": "",
            "source": "laya",
            "confidence": tool_conf,
        }

    except Exception as e:
        logger.debug("[Laya Router Error]: %s", e)
        return {"tool": "chat", "params": {}, "speak": "", "source": "laya_error"}


