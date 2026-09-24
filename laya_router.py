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
        "instructions": "Determine if `request` is a computer/device action command or conversational chat.",
        "criteria": {
            "action": "A command to launch an app, control windows, browse tabs, play music, adjust volume, take screenshot, set timer, or check emails",
            "chat": "General conversation, asking questions, seeking explanations, facts, math, jokes, greetings, or chit-chat",
        },
    },
    "tool": {
        "type": "choice",
        "instructions": "If `request` is an action, which tool should execute it?",
        "criteria": {
            "open_app": "Launch, start, run, or open an application or program",
            "close_app": "Close, quit, exit, or terminate an application or window",
            "window_mgmt": "Minimize all windows, maximize window, restore window, or switch window",
            "browser_nav": "Open a new tab, close current tab, switch tab, scroll down or up, visit URL",
            "desktop_input": "Type text, enter text, press key, shortcut, or click screen",
            "play_youtube": "Play music, songs, tracks, artist, audio, or video on YouTube",
            "set_volume": "Adjust volume, mute sound, raise volume, lower volume",
            "system_control": "Take screenshot, read screen, system status, lock PC, sleep PC, restart PC",
            "workspace": "Check emails, calendar events, search local files, set timer or reminder",
            "web_search": "Search Google or browse the web for information",
            "chat": "No action needed: pure conversational reply, knowledge, math, or explanation",
        },
    },
}

# High-priority device control matchers (instant, zero-overhead)
_RE_WEATHER = re.compile(r"\b(?:weather|forecast|temperature|rain|climate)\b", re.I)
_RE_TIMER = re.compile(r"\b(?:timer|countdown|alarm|stopwatch)\b", re.I)
_RE_EMAIL = re.compile(r"\b(?:email|emails|mail|inbox|outlook)\b", re.I)
_RE_CALENDAR = re.compile(r"\b(?:calendar|schedule|meeting|appointment)\b", re.I)
_RE_SCROLL = re.compile(r"\bscroll\s+(down|up)\b", re.I)
_RE_CLICK = re.compile(r"\b(?:left\s+|right\s+)?click(?:\s+(?:at|on)\s+(\d+)\s*[,x\s]\s*(\d+))?\b", re.I)
_RE_LOCK = re.compile(r"\block\s+(?:pc|computer|screen|workstation|my\s+pc)\b", re.I)
_RE_PRESS = re.compile(r"^(?:please\s+)?(?:press|hit)\s+['\"]?(.+?)['\"]?$", re.I)
_RE_TYPE = re.compile(r"^(?:please\s+)?(?:type|write)\s+['\"]?(.+?)['\"]?(?:\s+(?:in|into|on)\s+(?:the\s+)?(.+))?$", re.I)
_RE_VOL_NUM = re.compile(r"\b(\d{1,3})\s*(?:%|percent)?\b", re.I)
_RE_TIMER_SECS = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", re.I)


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


def extract_parameters_and_tool(tool: str, query: str) -> tuple[str, dict[str, Any]]:
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
        return "press_key", {"keys": "enter"}

    if tool == "play_youtube":
        clean = re.sub(r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:play|listen to|stream|put on|watch)\s+(?:the\s+|a\s+|some\s+)?", "", q, flags=re.I)
        clean = re.sub(r"\s+(?:on\s+youtube|please|for\s+me)$", "", clean, flags=re.I).strip()
        return "play_youtube", {"query": clean or q}

    if tool == "set_volume":
        if "mute" in q_low or "unmute" in q_low or "silence" in q_low:
            return "mute", {}
        if "louder" in q_low or "increase" in q_low or "turn up" in q_low:
            return "volume_up", {}
        if "quieter" in q_low or "decrease" in q_low or "turn down" in q_low:
            return "volume_down", {}
        m = _RE_VOL_NUM.search(q)
        return "set_volume", {"level": m.group(1) if m else "50"}

    if tool == "system_control":
        if "screenshot" in q_low:
            return "take_screenshot", {}
        if "lock" in q_low:
            return "lock_pc", {}
        if "sleep" in q_low:
            return "sleep_pc", {}
        if "restart" in q_low or "reboot" in q_low:
            return "restart_pc", {}
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


def route_intent_via_laya(user_query: str) -> dict[str, Any] | None:
    """
    Primary Router: High-speed System 1 neural decision routing via Laya.
    Evaluates intent in a single forward pass with zero latency conflict.
    Delegates conversational reasoning cleanly to MiniCPM 5 2B.
    """
    q = (user_query or "").strip()
    if not q or len(q) < 2:
        return {"tool": "chat", "params": {}, "speak": "", "source": "laya"}

    q_low = q.lower()

    # Fast-path shortcuts for unambiguous device utilities
    if _RE_WEATHER.search(q_low):
        m_city = re.search(r"\b(?:in|for|at|of)\s+([a-zA-Z\s.-]+)$", q, re.I)
        city = m_city.group(1).strip() if m_city else ""
        return {"tool": "get_weather", "params": {"city": city}, "speak": "", "source": "laya"}

    if _RE_TIMER.search(q_low):
        total_secs = 60
        if m := _RE_TIMER_SECS.search(q):
            val, unit = int(m.group(1)), m.group(2).lower()
            total_secs = val * 86400 if unit.startswith("d") else (val * 3600 if unit.startswith("h") else (val * 60 if unit.startswith("m") else val))
        return {"tool": "set_timer", "params": {"duration": total_secs, "seconds": total_secs}, "speak": "", "source": "laya"}

    if _RE_EMAIL.search(q_low):
        return {"tool": "unread_emails", "params": {}, "speak": "", "source": "laya"}

    if _RE_CALENDAR.search(q_low):
        return {"tool": "get_calendar", "params": {}, "speak": "", "source": "laya"}

    if _RE_LOCK.search(q_low):
        return {"tool": "lock_pc", "params": {}, "speak": "Locking your PC.", "source": "laya"}

    if m := _RE_PRESS.match(q):
        key_name = m.group(1).strip()
        return {"tool": "press_key", "params": {"keys": key_name}, "speak": f"Pressing {key_name}.", "source": "laya"}

    if m := _RE_TYPE.match(q):
        text = m.group(1).strip()
        app = m.group(2).strip() if m.group(2) else ""
        return {"tool": "type_text", "params": {"text": text, "app": app}, "speak": f"Typing into {app}." if app else f"Typing {text}.", "source": "laya"}

    if m := _RE_SCROLL.search(q_low):
        direction = m.group(1)
        return {"tool": f"scroll_{direction}", "params": {"amount": 600}, "speak": f"Scrolling {direction}.", "source": "laya"}

    if "new tab" in q_low:
        m_url = re.search(r"\b(?:for|to|with)\s+(\S+)", q, re.I)
        url = m_url.group(1) if m_url else ""
        return {"tool": "new_tab", "params": {"url": url}, "speak": "Opening new tab.", "source": "laya"}

    if "close tab" in q_low or "close this tab" in q_low:
        return {"tool": "close_tab", "params": {}, "speak": "Closing tab.", "source": "laya"}

    if "minimize all windows" in q_low or "minimize windows" in q_low:
        return {"tool": "window_management", "params": {"action": "minimize_all"}, "speak": "Minimizing all windows.", "source": "laya"}

    if "maximize" in q_low:
        return {"tool": "window_management", "params": {"action": "maximize"}, "speak": "Maximizing window.", "source": "laya"}

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
        conf = float(intent_info.get("confidence", 0.0))
        raw_tool = tool_info.get("choice", "chat")

        # Clean conversational escalation to MiniCPM 5 2B
        if intent == "chat" or conf < 0.35:
            logger.info("[Laya Router] Evaluated '%s' -> chat (conf=%.3f) in %.1f ms", q[:35], conf, elapsed_ms)
            return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": conf}

        if raw_tool == "chat":
            # If intent was action but tool argmax split over multiple action tools, pick best action tool
            tool_probs = tool_info.get("probabilities", {})
            non_chat_tools = {k: v for k, v in tool_probs.items() if k != "chat"}
            if non_chat_tools:
                best_action = max(non_chat_tools, key=non_chat_tools.get)
                if non_chat_tools[best_action] >= 0.15:
                    raw_tool = best_action
                else:
                    return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": conf}
            else:
                return {"tool": "chat", "params": {}, "speak": "", "source": "laya", "confidence": conf}

        final_tool, params = extract_parameters_and_tool(raw_tool, q)
        logger.info(
            "[Laya Router] Evaluated '%s' -> tool='%s' (conf=%.3f) in %.1f ms",
            q[:35], final_tool, conf, elapsed_ms,
        )

        return {
            "tool": final_tool,
            "params": params,
            "speak": "",
            "source": "laya",
            "confidence": conf,
        }

    except Exception as e:
        logger.debug("[Laya Router Error]: %s", e)
        return {"tool": "chat", "params": {}, "speak": "", "source": "laya_error"}
