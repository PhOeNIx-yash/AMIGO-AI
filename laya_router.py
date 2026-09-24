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
    "intent": {
        "type": "choice",
        "instructions": "Classify user intent: is the user asking the assistant to perform an action/task/lookup, or engaging in conversation/knowledge discussion?",
        "criteria": {
            "action": "Commands to perform a computer action: play or control music, launch apps, close windows, adjust settings, search Google, check live weather, set timers, or automate the PC",
            "chat": "Conceptual questions, asking how things work, seeking explanations, definitions, science, history, general conversation, greetings, math, jokes, stories, or advice",
        },
    },
    "tool": {
        "type": "choice",
        "instructions": "Which tool executes this request? If the request is for explanation, how things work, definitions, jokes, math, or conversation, choose chat.",
        "criteria": {
            "open_app": "Launch, start, run, or open an application or program",
            "close_app": "Close, quit, exit, terminate, or shut down an application, program, or window",
            "window_mgmt": "Minimize windows, maximize window, restore window, or switch window",
            "browser_nav": "Open new tab, close tab, switch tab, scroll down or up, visit website or URL",
            "desktop_input": "Type text into an app, press keys, shortcuts, or click screen",
            "play_youtube": "Play, replay, repeat, stream, or listen to songs, music, YouTube videos, or audio",
            "media_control": "Pause music, resume playback, next track, previous track, or stop media",
            "set_volume": "Adjust volume, mute sound, unmute audio, turn volume up or down",
            "get_weather": "Check live weather forecast, temperature, or rain for a city or location",
            "system_control": "Lock computer screen, sleep PC, restart PC, take screenshot, or read screen",
            "workspace": "Check unread emails, view calendar events, set timer or alarm, or search local files",
            "web_search": "Search Google, browse web, look up stock prices, live news, or real-time internet info",
            "chat": "Explanations, how things work, definitions, conceptual knowledge, reasoning, jokes, storytelling, math, or chit-chat",
        },
    },
}

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
        name = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+|an?\s+)?", "", q, flags=re.I)
        name = re.sub(r"\s+(?:please|for\s+me)$", "", name, flags=re.I).strip()
        return "open_app", {"name": name or "Notepad"}

    if tool == "close_app":
        name = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:close|quit|exit|kill)\s+(?:the\s+|an?\s+)?", "", q, flags=re.I)
        name = re.sub(r"\s+(?:please|for\s+me)$", "", name, flags=re.I).strip()
        return "close_app", {"name": name}

    if tool == "window_mgmt":
        if "maximize" in q_low:
            return "window_management", {"action": "maximize"}
        if "restore" in q_low:
            return "window_management", {"action": "restore"}
        if "switch" in q_low or "alt tab" in q_low:
            return "window_management", {"action": "switch_window"}
        return "window_management", {"action": "minimize_all"}

    if tool == "browser_nav":
        if "close tab" in q_low or "close this tab" in q_low:
            return "close_tab", {}
        if "next tab" in q_low:
            return "next_tab", {}
        if "prev tab" in q_low or "previous tab" in q_low:
            return "prev_tab", {}
        if m := _RE_SCROLL.search(q_low):
            return f"scroll_{m.group(1)}", {"amount": 600}
        m_url = re.search(r"\b(?:for|to|with)\s+(\S+)", q, re.I)
        url = m_url.group(1) if m_url else ""
        return "new_tab", {"url": url}

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
        clean = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:play|listen to|stream|put on|watch|replay|repeat)\s+(?:the\s+|a\s+|some\s+)?", "", q, flags=re.I)
        clean = re.sub(r"\s+(?:on\s+youtube|please|for\s+me)$", "", clean, flags=re.I).strip()
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
        if "mute" in q_low or "unmute" in q_low or "silence" in q_low:
            return "mute", {}
        if any(w in q_low for w in ("next", "skip")):
            return "next_track", {}
        if any(w in q_low for w in ("prev", "previous", "back")):
            return "prev_track", {}
        if any(w in q_low for w in ("pause", "stop", "freeze", "halt")):
            return "pause_media", {}
        return "play_media", {}

    if tool == "set_volume":
        if "mute" in q_low or "unmute" in q_low or "silence" in q_low:
            return "mute", {}
        if "louder" in q_low or "increase" in q_low or "turn up" in q_low:
            return "volume_up", {}
        if "quieter" in q_low or "decrease" in q_low or "turn down" in q_low:
            return "volume_down", {}
        m = _RE_VOL_NUM.search(q)
        return "set_volume", {"level": m.group(1) if m else "50"}

    if tool == "get_weather":
        m_city = re.search(r"\b(?:in|for|at|of)\s+([a-zA-Z\s.-]+)$", q, re.I)
        city = m_city.group(1).strip() if m_city else ""
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
        if "read screen" in q_low or "what is on my screen" in q_low:
            return "read_screen", {}
        return "system_status", {}

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
        clean = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:search\s+(?:for|google\s+for|the\s+web\s+for)?|google|look\s+up)\s+", "", q, flags=re.I)
        clean = re.sub(r"\s+(?:on\s+google|online|please)$", "", clean, flags=re.I).strip()
        return "web_search", {"query": clean or q}

    return tool, {}


def route_intent_via_laya(user_query: str, conversation_history: list | None = None) -> dict[str, Any] | None:
    """
    Primary Router: High-speed System 1 neural decision routing via Laya.
    Evaluates intent in a single forward pass with zero latency conflict.
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

        # Single parallel forward pass for both intent gating and tool prediction
        res = agent.predict(state, LAYA_QUESTIONS)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        intent_info = res.get("answers", {}).get("intent", {})
        tool_info = res.get("answers", {}).get("tool", {})

        intent = intent_info.get("choice", "chat")
        intent_conf = float(intent_info.get("confidence", 0.0))
        raw_tool = tool_info.get("choice", "chat")
        tool_conf = float(tool_info.get("confidence", 0.0))

        # Escalation to MiniCPM 5 2B when:
        # 1. Neural tool choice is 'chat'
        # 2. Classified as conversational chat AND tool is not a high-confidence lookup or media action
        if raw_tool == "chat":
            logger.info("[Laya Router] Evaluated '%s' -> chat in %.1f ms", q[:35], elapsed_ms)
            return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": intent_conf}

        if intent == "chat" and raw_tool not in ("get_weather", "play_youtube", "media_control", "set_volume"):
            logger.info("[Laya Router] Delegated '%s' (intent=chat) -> MiniCPM 5 2B in %.1f ms", q[:35], elapsed_ms)
            return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": intent_conf}

        final_tool, params = extract_parameters_and_tool(raw_tool, q, conversation_history=conversation_history)
        if final_tool == "chat":
            logger.info("[Laya Router] Extracted parameters evaluated '%s' -> chat in %.1f ms", q[:35], elapsed_ms)
            return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": intent_conf}

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

