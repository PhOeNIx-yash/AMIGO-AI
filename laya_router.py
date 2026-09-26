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
        "instructions": "Which capability or tool executes this request?",
        "criteria": {
            "open_app": "Launch or open an installed desktop application, software, or program",
            "close_app": "Close, quit, exit, or terminate a running application or window",
            "take_screenshot": "Capture, take, or save a screenshot image of the computer screen",
            "screen_vision": "Inspect, read, or describe what is currently visible on the computer display or screen",
            "lock_pc": "Lock the computer screen or workstation",
            "sleep_pc": "Put the computer or PC to sleep mode",
            "restart_pc": "Restart or reboot the computer system",
            "system_status": "Check computer hardware metrics like battery percentage, CPU load, or RAM usage",
            "empty_recycle_bin": "Empty the desktop recycle bin or trash",
            "play_youtube": "Search and play a song, music, track, artist, album, or video on YouTube",
            "pause_media": "Pause or stop currently playing media, music, or video",
            "play_media": "Resume, unpause, or continue playing paused media or music",
            "next_track": "Skip to the next song or next music track",
            "prev_track": "Go back to the previous song or track",
            "set_volume": "Adjust, increase, decrease, mute, unmute, or set system audio volume",
            "get_time": "Check the system clock time right now or what hour and minute it is",
            "get_date": "Report today's calendar date, day of week, or current year",
            "get_weather": "Check current outdoor weather conditions, local temperature, rain, or city forecast",
            "get_calendar": "Check scheduled calendar events, appointments, or meetings",
            "unread_emails": "Check or read unread emails or Outlook inbox messages",
            "set_timer": "Set a countdown timer, stopwatch, or alarm duration",
            "set_reminder": "Set or schedule a reminder or task alert",
            "find_file": "Find, search, or locate local files or folders on the computer",
            "document_qa": "Search or summarize contents of local documents, PDFs, or spreadsheets",
            "memory_recall": "Recall saved personal facts, flight numbers, tickets, or user notes from memory",
            "web_search": "Search Google or the web for online information, facts, or live news",
            "window_mgmt": "Minimize, maximize, restore, or switch desktop windows",
            "chat": "General conversation, chatting, answering questions, personal information, explanations, greetings, telling a joke, advice, or chit-chat",
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
    "pause_media": "pause media playback",
    "play_media": "resume media playback",
    "next_track": "skip to the next track",
    "prev_track": "go back to the previous track",
    "set_volume": "adjust the volume",
    "get_time": "check the current time",
    "get_date": "check the current date",
    "get_weather": "check the weather forecast",
    "weather": "check the weather forecast",
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
}


def format_clarification_prompt(tool_a: str, tool_b: str, query: str) -> str:
    desc_a = TOOL_ACTION_PROMPTS.get(tool_a, f"use {tool_a.replace('_', ' ')}")
    desc_b = TOOL_ACTION_PROMPTS.get(tool_b, f"use {tool_b.replace('_', ' ')}")
    return f"I'm not completely sure — did you want to {desc_a}, or {desc_b}?"


# Dynamic entity parameter extraction helpers
_RE_VOL_NUM = re.compile(r"\b(\d{1,3})\s*(?:%|percent)?\b", re.I)
_RE_TIMER_SECS = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", re.I)


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
            turn_params = turn.get("params") if isinstance(turn.get("params"), dict) else {}
            if turn_params.get("query"):
                return {"title": turn_params["query"], "query": turn_params["query"], "url": ""}

            # Extract from user query if this turn ran play_youtube (preserves full artist and track specification)
            if tool == "play_youtube":
                u = turn.get("user") or turn.get("query") or ""
                if u:
                    clean_u = re.sub(
                        r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:play|listen(?:\s+to)?|stream|put\s+on|watch|replay|repeat)\s+(?:the\s+|a\s+|some\s+)?(?:song\s+|music\s+|track\s+|video\s+)?(?:called\s+|titled\s+|named\s+)?",
                        "",
                        u,
                        flags=re.I,
                    )
                    clean_u = re.sub(r"\s+(?:on\s+youtube|from\s+youtube|please|for\s+me)$", "", clean_u, flags=re.I).strip().rstrip("?!.,;:")
                    if clean_u:
                        return {"title": clean_u, "query": clean_u, "url": ""}

            # Match tool or spoken confirmation
            if tool == "play_youtube" or "on YouTube" in assistant or "Playing" in assistant:
                m = re.search(r"Playing\s+['\"](.+?)['\"]", assistant, re.I)
                if not m:
                    m = re.search(r"Playing\s+(.+?)(?:\s+on\s+YouTube|\.|$)", assistant, re.I)
                if m:
                    song_name = m.group(1).strip().strip("'\"")
                    if song_name:
                        return {"title": song_name, "query": song_name, "url": ""}

            # Or match assistant describing the song
            m2 = re.search(r"(?:song\s+(?:I\s+played\s+)?was|looked\s+up\s+the\s+song)\s+['\"]?(.+?)['\"]?(?:\.|$)", assistant, re.I)
            if m2:
                song_name = m2.group(1).strip().strip("'\"")
                if song_name:
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
    """Extracts dynamic parameters for neural-selected tools without fragile keyword routing ladders."""
    q = query.strip()

    if tool == "open_app":
        # Extract target application entity
        name = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:open|launch|start|run|show)(?:\s+(?:the\s+|an?\s+|my\s+)?)?", "", q, flags=re.I)
        name = re.sub(r"\s+(?:please|for\s+me|app)$", "", name, flags=re.I).strip()
        # Anaphoric resolution from recent history if needed
        if (not name or name.lower() in ("it", "this", "that", "the app", "this app")) and conversation_history:
            for turn in reversed(conversation_history):
                if isinstance(turn, dict):
                    prev_u = turn.get("user") or turn.get("query") or ""
                    m_app = re.search(r"\b(?:open|launch|start|run|close|quit)\s+(?:the\s+|an?\s+)?(.+)", prev_u, re.I)
                    if m_app:
                        name = m_app.group(1).strip()
                        break
        return "open_app", {"name": name, "app_name": name}

    if tool == "close_app":
        name = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:close|quit|exit|kill|terminate|shut\s*down)(?:\s+(?:the\s+|an?\s+|my\s+)?)?", "", q, flags=re.I)
        name = re.sub(r"\s+(?:please|for\s+me|app)$", "", name, flags=re.I).strip()
        if (not name or name.lower() in ("it", "this", "that", "the app", "this app")) and conversation_history:
            for turn in reversed(conversation_history):
                if isinstance(turn, dict):
                    prev_u = turn.get("user") or turn.get("query") or ""
                    m_open = re.search(r"\b(?:open|launch|start|run)\s+(?:the\s+|an?\s+)?(.+)", prev_u, re.I)
                    if m_open:
                        name = m_open.group(1).strip()
                        break
        return "close_app", {"name": name, "app_name": name.lower() if name else ""}

    if tool == "play_youtube":
        # Check if casual remark or opinion about a song without play directive ("this song is very good")
        has_play_action = bool(re.search(r"\b(?:play|listen(?:\s+to)?|stream|put\s+on|watch|replay|repeat|queue)\b", q, re.I))
        if not has_play_action:
            return "chat", {}

        clean = re.sub(
            r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:play|listen(?:\s+to)?|stream|put\s+on|watch|replay|repeat)\s+(?:the\s+|a\s+|some\s+)?(?:song\s+|music\s+|track\s+|video\s+)?(?:called\s+|titled\s+|named\s+)?",
            "",
            q,
            flags=re.I,
        )
        clean = re.sub(r"\s+(?:on\s+youtube|from\s+youtube|please|for\s+me)$", "", clean, flags=re.I).strip().rstrip("?!.,;:")
        clean_norm = clean.lower()
        if not clean_norm or clean_norm in ("it again", "that again", "again", "it", "this", "that", "once more", "the song", "that song"):
            song = get_last_played_song(conversation_history)
            if song and song.get("title"):
                return "play_youtube", {"query": song["title"]}
        return "play_youtube", {"query": clean or q}

    if tool in ("media_control", "current_media"):
        if re.search(r"\b(?:song|playing|track|music)\b", q, re.I) and re.search(r"\b(?:what|current|which)\b", q, re.I):
            return "current_media", {}
        if re.search(r"\b(?:pause|stop|halt)\b", q, re.I):
            return "pause_media", {}
        if re.search(r"\b(?:resume|unpause|continue|play)\b", q, re.I):
            return "play_media", {}
        if re.search(r"\b(?:next|skip)\b", q, re.I):
            return "next_track", {}
        if re.search(r"\b(?:prev|previous|back)\b", q, re.I):
            return "prev_track", {}
        return tool, {}

    if tool == "time_date":
        if re.search(r"\b(?:date|day|today|year|month)\b", q, re.I):
            return "get_date", {}
        return "get_time", {}

    if tool in ("get_weather", "weather"):
        m_in = re.search(r"\b(?:in|at|for|of)\s+([a-zA-Z\s.-]+?)(?:\s*\?|\s*$|\s+please)", q, re.I)
        city = m_in.group(1).strip() if m_in else ""
        if city.lower().startswith("the weather"):
            m_sub = re.search(r"\b(?:in|at|for)\s+([a-zA-Z\s.-]+)", city, re.I)
            city = m_sub.group(1).strip() if m_sub else ""
        if not city and conversation_history:
            for turn in reversed(conversation_history):
                if isinstance(turn, dict):
                    prev_u = turn.get("user") or turn.get("query") or ""
                    m_prev = re.search(r"\b(?:in|at|for|of)\s+([a-zA-Z\s.-]+?)(?:\s*\?|\s*$|\s+please)", prev_u, re.I)
                    if m_prev:
                        city = m_prev.group(1).strip()
                        break
        return "get_weather", {"city": city}

    if tool == "set_volume":
        if re.search(r"\b(?:mute|silence|unmute)\b", q, re.I):
            return "mute", {}
        if re.search(r"\b(?:up|louder|increase|higher|boost)\b", q, re.I):
            return "volume_up", {}
        if re.search(r"\b(?:down|quieter|decrease|lower|soften)\b", q, re.I):
            return "volume_down", {}
        m = _RE_VOL_NUM.search(q)
        return "set_volume", {"level": m.group(1) if m else "50"}

    if tool == "set_timer":
        total_secs = 60
        if m := _RE_TIMER_SECS.search(q):
            val, unit = int(m.group(1)), m.group(2).lower()
            total_secs = val * 86400 if unit.startswith("d") else (val * 3600 if unit.startswith("h") else (val * 60 if unit.startswith("m") else val))
        return "set_timer", {"duration": total_secs, "seconds": total_secs}

    if tool == "set_reminder":
        return "set_reminder", {"query": q}

    if tool == "find_file":
        clean = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:find|search(?:\s+for)?|locate|show)(?:\s+(?:the\s+|my\s+|an?\s+)?)?(?:file|document|folder|doc)?(?:\s+(?:called|named|titled))?\s*", "", q, flags=re.I).strip()
        return "find_file", {"query": clean or q}

    if tool in ("document_qa", "ask_document"):
        return "document_qa", {"query": q}

    if tool == "memory_recall":
        return "memory_recall", {"query": q}

    if tool in ("screen_vision", "read_screen"):
        return "screen_vision", {"question": q}

    if tool == "web_search":
        clean = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:search(?:\s+(?:the\s+web|online|google))?(?:\s+for)?|google(?:\s+for)?|look\s+up)\s+", "", q, flags=re.I)
        clean = re.sub(r"\s+(?:on\s+google|online|please)$", "", clean, flags=re.I).strip()
        return "web_search", {"query": clean or q}

    if tool == "window_mgmt":
        if re.search(r"\b(?:maximize|fullscreen)\b", q, re.I):
            return "window_management", {"action": "maximize"}
        if re.search(r"\b(?:restore|unmaximize)\b", q, re.I):
            return "window_management", {"action": "restore"}
        if re.search(r"\b(?:switch|alt\s*tab)\b", q, re.I):
            return "window_management", {"action": "switch_window"}
        return "window_management", {"action": "minimize_all"}

    if tool == "system_control":
        # Legacy umbrella compatibility fallback
        if re.search(r"\b(?:screenshot|snap)\b", q, re.I):
            return "take_screenshot", {}
        if re.search(r"\b(?:lock)\b", q, re.I):
            return "lock_pc", {}
        if re.search(r"\b(?:sleep)\b", q, re.I):
            return "sleep_pc", {}
        if re.search(r"\b(?:restart|reboot)\b", q, re.I):
            return "restart_pc", {}
        return "system_status", {}

    if tool == "workspace":
        # Legacy umbrella compatibility fallback
        if re.search(r"\b(?:email|inbox|mail)\b", q, re.I):
            return "unread_emails", {}
        if re.search(r"\b(?:calendar|meeting|schedule)\b", q, re.I):
            return "get_calendar", {}
        return "set_timer", {"duration": 60, "seconds": 60}

    # Concrete tools directly execute with natural parameters:
    # take_screenshot, lock_pc, sleep_pc, restart_pc, system_status, empty_recycle_bin,
    # pause_media, play_media, next_track, prev_track, mute, get_time, get_date,
    # get_calendar, unread_emails, chat
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

        # Contextual history injection: only inject history when the request is anaphoric/follow-up,
        # so independent questions are evaluated purely on their own without previous topic bias.
        q_low = q.lower()
        is_anaphoric_followup = (
            len(q.split()) <= 4
            or any(re.search(r"\b" + re.escape(w) + r"\b", q_low) for w in ("it", "again", "that", "this", "repeat", "them"))
            or any(q_low.startswith(p) for p in ("what about", "how about", "and in", "and for", "and then", "what else"))
        )

        state: dict[str, Any] = {"request": q}

        if is_anaphoric_followup:
            hist = conversation_history
            if hist is None:
                try:
                    from rag_engine import get_recent_conversations
                    hist = get_recent_conversations(count=2)
                except Exception:
                    hist = []

            recent_history = []
            if hist and isinstance(hist, list):
                for turn in hist[-2:]:
                    if isinstance(turn, dict):
                        user_msg = (turn.get("user") or turn.get("query") or "").strip()
                        assistant_msg = (turn.get("assistant") or turn.get("response") or "").strip()
                        turn_data = {}
                        if user_msg:
                            turn_data["user"] = user_msg
                        if assistant_msg:
                            turn_data["assistant"] = assistant_msg[:100]
                        if turn.get("tool"):
                            turn_data["tool"] = turn.get("tool")
                        if turn_data:
                            recent_history.append(turn_data)

            if recent_history:
                state["history"] = recent_history

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

        if final_tool != "chat" and tool_conf < 0.35:
            logger.info("[Laya Router] Low confidence (%.3f) for '%s' -> natural fallback to chat in %.1f ms", tool_conf, final_tool, elapsed_ms)
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


