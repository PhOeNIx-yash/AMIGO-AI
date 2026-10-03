"""
LLM-Based Agent for Amigo Voice Assistant.
Provides natural language understanding using MiniCPM 5 2B with tool calling for action execution.
"""

import json
import logging
import re
import threading
from typing import Generator

from amigo.core.local_llm import (
    query_local_llm,
    query_local_llm_stream,
    init_local_llm,
    sanitize_for_tts,
    is_thinking_enabled,
)
from amigo.core.ai import get_user_profile_prompt, get_active_context_prompt, get_active_state
import amigo.core.rag_engine as rag_engine
from amigo.core.rag_engine import get_recent_conversations
from amigo.utils.tool_registry import execute_tool
from amigo.utils.network_utils import is_internet_connected

logger = logging.getLogger("amigo.llm_agent")

# ──────────────────────────────────────────────────────────────────────────────
# Tool Definitions for Function Calling
# ──────────────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "chat",
        "description": "General conversation, questions, explanations, advice, coding help, greetings, jokes. Do NOT use for WRITE/TYPE/GENERATE/COMPOSE requests - use 'type_text' or 'generate_content'.",
        "parameters": {
            "type": "object",
            "properties": {
                "response": {"type": "string", "description": "The conversational response to the user"}
            },
            "required": ["response"]
        }
    },
    {
        "name": "open_app",
        "description": "Launch an installed application by name (e.g., 'vscode', 'chrome', 'calculator', 'word').",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Application name to open"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "close_app",
        "description": "Close a running application by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Application name to close"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "play_youtube",
        "description": "Play music, songs, videos, or audio on YouTube. Use this for ANY request to play music, stream a song, listen to a track, or watch a video. This works online via YouTube - you DO have access to this. Do NOT say you cannot play music.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search and play on YouTube (song name, artist, video title, etc.)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for information, facts, news, or current events.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_weather",
        "description": "Check current weather conditions, temperature, or forecast for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City or location name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "get_time",
        "description": "Check the current system time.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "get_date",
        "description": "Report today's date, day of week, or current year.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "set_volume",
        "description": "Adjust system audio volume. Valid actions: mute, volume_up, volume_down, set_volume (with level 0-100).",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["mute", "volume_up", "volume_down", "set_volume"], "description": "Volume action: mute, volume_up, volume_down, or set_volume"},
                "level": {"type": "integer", "description": "Volume level 0-100 (required when action is set_volume)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "pause_media",
        "description": "Pause currently playing media or music.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "play_media",
        "description": "Resume paused media or music.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "next_track",
        "description": "Skip to the next song/track.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "prev_track",
        "description": "Go back to the previous song/track.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "current_media",
        "description": "Check what song/track is currently playing.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "window_mgmt",
        "description": "Minimize, maximize, restore, or switch windows.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["minimize_all", "maximize", "restore", "switch_window"], "description": "Window action"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "take_screenshot",
        "description": "Capture a screenshot of the screen.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "lock_pc",
        "description": "Lock the computer screen.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "sleep_pc",
        "description": "Put the computer to sleep.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "restart_pc",
        "description": "Restart the computer.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "get_calendar",
        "description": "Check the user's LOCAL Outlook calendar events, appointments, or meetings. This accesses the user's calendar data stored locally on their machine - NO cloud access.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "unread_emails",
        "description": "Check the user's LOCAL Outlook unread emails or inbox. This accesses emails stored locally on the user's machine - NO cloud access.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer or alarm.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Timer duration and description (e.g., '5 minutes', '1 hour')"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "set_reminder",
        "description": "Set a reminder or task alert.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Reminder text and when (e.g., 'call mom in 30 minutes', 'meeting at 3pm')"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "find_file",
        "description": "Find or locate local files or folders on the user's computer. This searches the user's LOCAL file system - NO cloud access.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "File or folder name to search for"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "document_qa",
        "description": "Search, read, or summarize the user's LOCAL DOCUMENTS and FILES on their computer (PDFs, Word docs, text files, spreadsheets, presentations). This accesses files stored locally on the user's machine - NO cloud access. Use when user asks about content from their files, documents, tickets, bookings, confirmations, receipts, PNR numbers, flight details, hotel reservations, or any information that would be in a saved document/file.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question about document/file contents (e.g., 'what is my PNR number', 'show my flight booking', 'read the PDF')"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_recall",
        "description": "Recall saved PERSONAL FACTS, preferences, or conversation history that the user explicitly told you to remember (e.g., 'remember my name is John', 'I like coffee', 'my birthday is...'). This accesses the user's LOCAL memory database on their machine. Use for facts the user SAVED TO MEMORY, not for content from documents/files.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What personal fact or preference to recall from memory"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "type_text",
        "description": "Type text into the active window or a specific app. Use for WRITE/TYPE/INPUT requests. If no app specified, types into current window.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
                "app": {"type": "string", "description": "Optional: specific app (e.g., 'vscode', 'word'). Only if user explicitly mentions."}
            },
            "required": ["text"]
        }
    },
    {
        "name": "generate_content",
        "description": "Generate content (email, letter, application, code, document, message) using AI and show in review panel. Use for GENERATE/CREATE/COMPOSE/DRAFT requests. Params: type, topic, context (optional).",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Content type (email, letter, application, code, document, message)"},
                "topic": {"type": "string", "description": "What to write about"},
                "context": {"type": "string", "description": "Optional additional context"}
            },
            "required": ["type", "topic"]
        }
    },
    {
        "name": "insert_content",
        "description": "Insert generated content into active window via clipboard paste (Ctrl+V). Use when user says 'insert it', 'type it here', 'paste it'. Content must be from generate_content.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to insert"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "system_status",
        "description": "Check computer hardware metrics like battery percentage, CPU load, or RAM usage.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "empty_recycle_bin",
        "description": "Empty the desktop recycle bin or trash.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key or shortcut (e.g., enter, escape, tab, ctrl+s, ctrl+c, ctrl+v, ctrl+a, ctrl+z).",
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Key or shortcut to press (e.g., 'enter', 'ctrl+s', 'escape', 'tab')"}
            },
            "required": ["keys"]
        }
    },
    {
        "name": "click_screen",
        "description": "Click at a specific screen coordinate or the current mouse position.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (optional)"},
                "y": {"type": "integer", "description": "Y coordinate (optional)"}
            }
        }
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# System Prompt for the Agent
# ──────────────────────────────────────────────────────────────────────────────

def build_agent_system_prompt(query: str = "", conversation_history: list | None = None) -> str:
    """Build the system prompt for the LLM agent with full context."""
    from datetime import datetime
    now = datetime.now()
    online = is_internet_connected()
    net_status = "Connected (Online)" if online else "Disconnected (Offline)"

    # Get user profile and active context
    user_profile = get_user_profile_prompt(query)
    active_context = get_active_context_prompt()

    # Get recent conversation history - use passed history if available, otherwise from RAG
    if conversation_history:
        recent = conversation_history[-10:]  # Use last 10 turns from passed history
        last_s = get_last_played_song(conversation_history)
        if last_s and last_s.get("title") and "Playing" not in active_context:
            if active_context:
                active_context = active_context.rstrip("]") + f" | Last Media: '{last_s['title']}']"
            else:
                active_context = f"[Active State: Last Media: '{last_s['title']}']"
    else:
        recent = rag_engine.get_recent_conversations(10)
    history_lines = []
    for turn in recent:
        u = (turn.get("user") or "").strip()
        a = (turn.get("assistant") or "").strip()
        if u:
            history_lines.append(f"User: {u}")
        if a:
            history_lines.append(f"Assistant: {a[:200]}")
    history_block = "\n".join(history_lines) if history_lines else "No recent conversation."

    # Build concise tool schema for the prompt
    tool_schema_lines = []
    for tool in TOOL_DEFINITIONS:
        name = tool["name"]
        desc = tool["description"]
        props = tool["parameters"]["properties"]
        required = tool["parameters"].get("required", [])
        if not props:
            tool_schema_lines.append(f"- {name}: {desc} | Params: none (use empty {{}})")
        else:
            param_details = [f"{k}: {v.get('type', 'string')}{' (req)' if k in required else ''}" for k, v in props.items()]
            tool_schema_lines.append(f"- {name}: {desc} | Params: {', '.join(param_details)}")
    tools_block = "\n".join(tool_schema_lines)

    hour = now.hour
    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 21:
        period = "evening"
    else:
        period = "night"
    current_time_str = now.strftime('%I:%M %p').lstrip('0')

    prompt = f"""You are Amigo, an intelligent Windows desktop voice assistant with active tool execution capabilities.
Today is {now.strftime('%A, %B %d, %Y')}. Current local time: {current_time_str} ({period}).
Internet Status: {net_status}.
Be naturally aware of the current local time and period of day (morning, afternoon, evening, night) when conversing or greeting the user.

YOUR PERSONALITY:
You are a capable, natural voice assistant - helpful and conversational, not robotic.
- Talk like a competent colleague: clear, varied, and to the point.
- Vary your language every time: different greetings, confirmations, and phrasing.
- Keep it concise: 1-2 sentences for most responses, more only when needed.
- Acknowledge the user's request naturally before acting or answering.
- Never use the same opener twice - rotate through: "Sure thing", "Got it", "On it", "Right away", "Done", "All set", "Here you go", etc.
- Match the user's tone: efficient for commands, warm for questions, direct for facts.
- Stay accurate - be helpful, not entertaining.

You have access to the following tools. When the user asks you to do something, you MUST decide which tool(s) to use and call them with the appropriate parameters.

AVAILABLE TOOLS:
{tools_block}

CONVERSATION HISTORY (most recent last):
{history_block}
{active_context}
{user_profile}

OUTPUT FORMAT:
Every response MUST be a JSON object:
{{"tool": "tool_name", "params": {{...}}, "speak": "optional confirmation"}}

CONTEXT RESOLUTION:
When the user refers to something from recent conversation (e.g., "play it again", "play that song", "open that file", "search for that", "the same", "again", "it", "that"), you MUST resolve the reference using the CONVERSATION HISTORY and ACTIVE STATE above. Look at the most recent relevant user request and use those details in your tool parameters.

SPECIFIC RESOLUTION RULES:
- "play it again", "play that again", "replay", "repeat" -> Use the song from ACTIVE STATE "Last Media" or the most recent "play_youtube" in CONVERSATION HISTORY
- "open that", "open it" -> Use the app/file from the most recent "open_app" or "find_file" in CONVERSATION HISTORY
- "search for that", "search that" -> Use the query from the most recent "web_search" in CONVERSATION HISTORY
- "close that", "close it" -> Use the app from the most recent "open_app" in CONVERSATION HISTORY

INSTRUCTIONS FOR SELECTING TOOLS:
- For ANY request to play music, songs, tracks, artists, or audio on YouTube (e.g. "play X", "play X on youtube"): ALWAYS use "play_youtube" with {{"query": "song name and artist"}}
- To resume playback: use "play_media". To pause music: use "pause_media"
- For next song/track: use "next_track". For previous track/song: use "prev_track"
- To check what song is currently playing: use "current_media"
- For volume (mute, unmute, turn up, turn down, set to number): ALWAYS use "set_volume" with {{"action": "mute"|"volume_up"|"volume_down"|"set_volume", "level": N}}
- To take a screenshot or capture the screen: ALWAYS use "take_screenshot" with {{}}
- To open or launch apps: use "open_app" with {{"name": "app name"}}
- To close or exit apps: use "close_app" with {{"name": "app name"}}
- To minimize, maximize, restore windows: use "window_mgmt" with {{"action": "minimize_all" | "maximize" | "restore" | "switch_window"}}
- To lock PC: use "lock_pc". To put PC to sleep: use "sleep_pc". To restart PC: use "restart_pc"
- To empty recycle bin: use "empty_recycle_bin"
- To check system status, battery, CPU, RAM: use "system_status"
- To search the web: ALWAYS use "web_search" with {{"query": "..."}}
- To search or find local files: ALWAYS use "find_file" with {{"query": "..."}}
- To ask about, read, or summarize documents, tickets, or files: use "document_qa" with {{"query": "..."}}
- To check calendar events or schedule: ALWAYS use "get_calendar"
- To check unread emails: ALWAYS use "unread_emails"
- To set timers: use "set_timer" with {{"query": "..."}}
- To set reminders: use "set_reminder" with {{"query": "..."}}
- For weather forecast: use "get_weather" with {{"city": "CityName"}} (Capitalize city name)
- For general conversation, greeting, jokes, or explanations: output {{"tool": "chat", "params": {{"response": "your answer"}}, "speak": "your answer"}}

EXAMPLES:
User: what time is it
Assistant: {{"tool": "get_time", "params": {{}}}}

User: what's the date today
Assistant: {{"tool": "get_date", "params": {{}}}}

User: play believer by imagine dragons on youtube
Assistant: {{"tool": "play_youtube", "params": {{"query": "believer by imagine dragons"}}}}

User: pause the music
Assistant: {{"tool": "pause_media", "params": {{}}}}

User: resume playback
Assistant: {{"tool": "play_media", "params": {{}}}}

User: search for python tutorials
Assistant: {{"tool": "web_search", "params": {{"query": "python tutorials"}}}}

User: find file report.pdf
Assistant: {{"tool": "find_file", "params": {{"query": "report.pdf"}}}}

User: check my calendar
Assistant: {{"tool": "get_calendar", "params": {{}}}}

User: lock my pc
Assistant: {{"tool": "lock_pc", "params": {{}}}}

User: check system status
Assistant: {{"tool": "system_status", "params": {{}}}}

User: empty recycle bin
Assistant: {{"tool": "empty_recycle_bin", "params": {{}}}}

User: close chrome
Assistant: {{"tool": "close_app", "params": {{"name": "chrome"}}}}

User: next song
Assistant: {{"tool": "next_track", "params": {{}}}}

User: previous track
Assistant: {{"tool": "prev_track", "params": {{}}}}

User: what song is playing
Assistant: {{"tool": "current_media", "params": {{}}}}

User: set volume to 50
Assistant: {{"tool": "set_volume", "params": {{"action": "set_volume", "level": 50}}}}

User: mute the volume
Assistant: {{"tool": "set_volume", "params": {{"action": "mute"}}}}

User: turn up the volume
Assistant: {{"tool": "set_volume", "params": {{"action": "volume_up"}}}}

User: turn down the volume
Assistant: {{"tool": "set_volume", "params": {{"action": "volume_down"}}}}

User: take a screenshot
Assistant: {{"tool": "take_screenshot", "params": {{}}}}

User: what's the weather in london
Assistant: {{"tool": "get_weather", "params": {{"city": "London"}}}}

User: minimize all windows
Assistant: {{"tool": "window_mgmt", "params": {{"action": "minimize_all"}}}}

User: maximize this window
Assistant: {{"tool": "window_mgmt", "params": {{"action": "maximize"}}}}

User: put pc to sleep
Assistant: {{"tool": "sleep_pc", "params": {{}}}}

User: restart computer
Assistant: {{"tool": "restart_pc", "params": {{}}}}

User: remind me to call mom in 30 minutes
Assistant: {{"tool": "set_reminder", "params": {{"query": "call mom in 30 minutes"}}}}

User: what does this document say
Assistant: {{"tool": "document_qa", "params": {{"query": "what does this document say"}}}}

User: how are you today
Assistant: {{"tool": "chat", "params": {{"response": "I'm doing well, thank you! How can I help you today?"}}}}

User: tell me a joke
Assistant: {{"tool": "chat", "params": {{"response": "Why do programmers prefer dark mode? Because light attracts bugs!"}}}}
"""
    return prompt


# ──────────────────────────────────────────────────────────────────────────────
# Agent Core Logic
# ──────────────────────────────────────────────────────────────────────────────

_agent_lock = threading.Lock()
_agent_initialized = False


def initialize_agent() -> bool:
    """Initialize the LLM agent (loads the local model)."""
    global _agent_initialized
    with _agent_lock:
        if _agent_initialized:
            return True
        try:
            init_local_llm()
            _agent_initialized = True
            logger.info("[LLM Agent] Initialized successfully")
            return True
        except Exception as e:
            logger.error(f"[LLM Agent] Initialization failed: {e}")
            return False


def _parse_tool_calls(response: str) -> list[dict]:
    """Parse tool calls from LLM response. Supports JSON and XML tool calls formats."""
    tool_calls = []

    # 1. Depth-based balanced-brace parser for JSON blocks
    start = 0
    text = response
    while True:
        idx = text.find('{', start)
        if idx == -1:
            break
        depth = 0
        end = -1
        in_string = False
        escape = False
        for i in range(idx, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end != -1:
            chunk = text[idx:end]
            try:
                data = json.loads(chunk)
                if isinstance(data, dict):
                    tool_name = data.get("tool") or data.get("name")
                    if tool_name:
                        if tool_name == "window_management":
                            tool_name = "window_mgmt"
                        params = data.get("params") or data.get("arguments", {})
                        speak = data.get("speak", "")
                        tool_calls.append({
                            "tool": tool_name,
                            "params": params if isinstance(params, dict) else {},
                            "speak": speak if isinstance(speak, str) else "",
                        })
            except Exception:
                pass
            start = end
        else:
            start = idx + 1

    if tool_calls:
        return tool_calls

    # 2. MiniCPM native XML format: <function name="tool_name"><param name="param_name">param_value</param></function>
    xml_matches = re.finditer(r'<function\s+name="([^"]+)">([\s\S]*?)</function>', response)
    for match in xml_matches:
        tool_name = match.group(1).strip()
        if tool_name == "window_management":
            tool_name = "window_mgmt"
        body = match.group(2)
        params = {}
        for param_m in re.finditer(r'<param\s+name="([^"]+)">([\s\S]*?)</param>', body):
            p_val = param_m.group(2).strip()
            if p_val.startswith("<![CDATA[") and p_val.endswith("]]>"):
                p_val = p_val[9:-3]
            params[param_m.group(1).strip()] = p_val
        tool_calls.append({
            "tool": tool_name,
            "params": params,
            "speak": "",
        })

    if not tool_calls:
        # Fallback legacy regex pattern
        tool_name_match = re.search(r'name="([^"]+)">', response)
        if tool_name_match:
            tool_name = tool_name_match.group(1).strip()
            if tool_name == "window_management":
                tool_name = "window_mgmt"
            params = {}
            remaining = response[tool_name_match.end():]
            param_pattern = r'name="([^"]+)">\s*([^<]*?)(?=\s+name="|$)'
            param_matches = re.findall(param_pattern, remaining)
            for param_name, param_value in param_matches:
                if param_value:
                    params[param_name] = param_value.strip()
            if tool_name:
                tool_calls.append({
                    "tool": tool_name,
                    "params": params,
                    "speak": "",
                })

    return tool_calls


def _resolve_references(query: str, conversation_history: list | None = None) -> str:
    """Preprocess query to resolve common references like 'it', 'that', 'again' using conversation history."""
    if not conversation_history:
        return query

    query_lower = query.lower().strip()

    # "play it again", "play that again", "replay", "repeat" -> use last played song
    if any(phrase in query_lower for phrase in ["play it again", "play that again", "replay", "repeat the song", "play again"]):
        last_song = get_last_played_song(conversation_history)
        if last_song and last_song.get("title"):
            return f"play {last_song['title']}"

    # "open that", "open it" -> use last opened app/file
    if query_lower in ("open that", "open it", "open this"):
        for turn in reversed(conversation_history):
            tool = turn.get("tool", "")
            if tool == "open_app":
                app_name = turn.get("params", {}).get("name", "")
                if app_name:
                    return f"open {app_name}"
            elif tool == "find_file":
                file_query = turn.get("params", {}).get("query", "")
                if file_query:
                    return f"open {file_query}"

    # "close that", "close it" -> use last opened app
    if query_lower in ("close that", "close it", "close this"):
        for turn in reversed(conversation_history):
            tool = turn.get("tool", "")
            if tool == "open_app":
                app_name = turn.get("params", {}).get("name", "")
                if app_name:
                    return f"close {app_name}"

    # "search for that", "search that" -> use last search query
    if query_lower in ("search for that", "search that", "search this"):
        for turn in reversed(conversation_history):
            tool = turn.get("tool", "")
            if tool == "web_search":
                search_query = turn.get("params", {}).get("query", "")
                if search_query:
                    return f"search for {search_query}"

    return query


def get_agent_actions(
    query: str,
    conversation_history: list | None = None,
        ) -> list[dict] | tuple[list[dict], str]:
    """
    Main entry point: Get agent actions for a user query using LLM reasoning.
    Returns a list of tool actions to execute.
    The LLM will decide whether to use one or multiple tools based on the user's intent.
    """
    if not initialize_agent():
        return [{"tool": "chat", "params": {}, "speak": "I'm having trouble initializing my AI engine. Please try again."}]

    # Preprocess query to resolve references like "it", "that", "again"
    resolved_query = _resolve_references(query, conversation_history)

    # Build system prompt with full context
    system_prompt = build_agent_system_prompt(resolved_query, conversation_history=conversation_history)

    # Build conversation messages including history
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add conversation history as proper chat messages
    if conversation_history:
        recent = conversation_history[-10:]  # Use last 10 turns
        for turn in recent:
            u = (turn.get("user") or "").strip()
            a = (turn.get("assistant") or "").strip()
            if u:
                messages.append({"role": "user", "content": u[:1000]})
            if a:
                messages.append({"role": "assistant", "content": a[:1500]})
    
    # Add current query (use resolved query for LLM)
    messages.append({"role": "user", "content": resolved_query})

    try:
        response = query_local_llm(
    messages,
    system_prompt="",  # System prompt is already in messages
    max_tokens=256,
    temperature=0.0,
    thinking=False,
    sanitize=False,
    response_format={"type": "json_object"},
        )

        # Parse tool calls from response
        tool_calls = _parse_tool_calls(response)

        # Extract final conversational response (after tool calls)
        final_response = _extract_final_response(response, tool_calls)

        # If no tool calls found, default to chat
        if not tool_calls:
            # Check if it's a probe query (like "are you there")
            from amigo.core.local_llm import _RE_PROBE_GUARD
            if _RE_PROBE_GUARD.search(query):
                return [{"tool": "chat", "params": {}, "speak": ""}], ""
            return [{"tool": "chat", "params": {"response": final_response}, "speak": final_response}], final_response

        # Return both tool calls and final response
        return tool_calls, final_response

    except Exception as e:
        logger.error(f"[LLM Agent] Error getting actions: {e}")
        return [{"tool": "chat", "params": {}, "speak": "I encountered an error processing your request."}], "I encountered an error processing your request."


def _extract_final_response(response: str, tool_calls: list) -> str:
    """Extract the final conversational response after tool calls."""
    import re
    import json
    
    cleaned = response
    
    # Remove tool call JSON by serializing parsed tool_calls back to JSON and removing exact matches
    for call in tool_calls:
        # Format 1: {"tool": "...", "params": {...}, "speak": "..."} - with spaces
        tool_json1 = json.dumps({"tool": call["tool"], "params": call.get("params", {}), "speak": call.get("speak", "")})
        # Format 2: compact (no spaces)
        tool_json2 = json.dumps({"tool": call["tool"], "params": call.get("params", {}), "speak": call.get("speak", "")}, separators=(',', ':'))
        # Format 3: function calling style
        tool_json3 = json.dumps({"name": call["tool"], "arguments": call.get("params", {})})
        tool_json4 = json.dumps({"name": call["tool"], "arguments": call.get("params", {})}, separators=(',', ':'))
        
        for tj in [tool_json1, tool_json2, tool_json3, tool_json4]:
            cleaned = cleaned.replace(tj, '')
    
    # Fallback: regex for any remaining tool call patterns
    cleaned = re.sub(r'\{[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', '', cleaned)
    cleaned = re.sub(r'\{[^{}]*"name"\s*:\s*"[^"]+"[^{}]*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}', '', cleaned)
    cleaned = re.sub(r'\{[^{}]*"(?:tool|name)"\s*:\s*"[^"]+"[^{}]*\}', '', cleaned)
    
    # Remove ```...``` blocks
    cleaned = re.sub(r'```[\s\S]*?```', '', cleaned)
    cleaned = re.sub(r'```', '', cleaned)
    # Remove any leftover "speak": "..." fields
    cleaned = re.sub(r'"speak"\s*:\s*"[^"]*"', '', cleaned)
    # Clean up extra whitespace and punctuation
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'^[,\s]+|[,\s]+$', '', cleaned)
    return sanitize_for_tts(cleaned)


def get_agent_response_stream(
    query: str,
    conversation_history: list | None = None,
    interruption_event: threading.Event | None = None
) -> Generator[str, None, None]:
    """
    Streaming version for real-time responses.
    Yields sentences as they're generated.
    """
    if not initialize_agent():
        yield "I'm having trouble initializing my AI engine. Please try again."
        return

    system_prompt = build_agent_system_prompt(query, conversation_history=conversation_history)

    messages = [{"role": "system", "content": system_prompt}]
    if conversation_history:
        recent = conversation_history[-10:]
        for turn in recent:
            u = (turn.get("user") or "").strip()
            a = (turn.get("assistant") or "").strip()
            if u:
                messages.append({"role": "user", "content": u[:1000]})
            if a:
                messages.append({"role": "assistant", "content": a[:1500]})
    messages.append({"role": "user", "content": query})

    try:
        token_gen = query_local_llm_stream(
            messages,
            system_prompt="",
            max_tokens=1024,
            temperature=0.1,
            thinking=is_thinking_enabled(),
            interruption_event=interruption_event
        )

        # Collect full response first to parse tool calls
        full_response = ""
        for token in token_gen:
            full_response += token

        # Parse and execute tool calls
        tool_calls = _parse_tool_calls(full_response)

        if tool_calls:
            # Execute tools and yield results
            for action in tool_calls:
                tool = action.get("tool", "chat")
                params = action.get("params", {})
                speak = action.get("speak", "")

                if speak:
                    yield speak

                if tool != "chat":
                    spoken, url, meta = execute_tool(tool, params, query=query, spoken=speak)
                    if spoken and spoken != speak:
                        yield spoken

            # Yield final conversational response
            final_response = _extract_final_response(full_response, tool_calls)
            if final_response:
                yield final_response
        else:
            # Just chat response
            final_response = _extract_final_response(full_response, [])
            if final_response:
                yield final_response

    except Exception as e:
        logger.error(f"[LLM Agent] Streaming error: {e}")
        yield "I encountered an error processing your request."


# ──────────────────────────────────────────────────────────────────────────────
# Compatibility Layer for Existing Code
# ──────────────────────────────────────────────────────────────────────────────

def get_agent_action(query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Main function to resolve user query into tool actions.
    Returns only the tool actions (first element of tuple).
    """
    result = get_agent_actions(query, conversation_history)
    if isinstance(result, tuple):
        actions, _ = result
        return actions
    return result


def get_agent_actions_with_response(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    """
    Get agent actions AND the final conversational response from the LLM.
    Returns tuple of (actions, final_response).
    """
    result = get_agent_actions(query, conversation_history)
    if isinstance(result, tuple):
        actions, final_response = result
        return actions, final_response
    return result, ""


# ──────────────────────────────────────────────────────────────────────────────
# Clarification Prompt (for ambiguous requests)
# ──────────────────────────────────────────────────────────────────────────────

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
    return f"I'm not completely sure — did you want to {desc_a}, or {desc_b}?"


# ──────────────────────────────────────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────────────────────────────────────

def get_last_played_song(conversation_history: list | None = None) -> dict | None:
    """Finds the most recently played media title/query from active state, UI server, or history."""
    # 1. Check in-memory active state
    try:
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
        from importlib import import_module
        ui_server = import_module("ui_server")
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
            hist = get_recent_conversations(count=10)
        except Exception:
            hist = []

    if hist:
        for turn in reversed(hist):
            if not isinstance(turn, dict):
                continue
            tool = turn.get("tool")
            assistant = turn.get("assistant") or turn.get("response") or ""
            params = turn.get("params")
            turn_params = params if isinstance(params, dict) else {}
            if turn_params.get("query"):
                return {"title": turn_params["query"], "query": turn_params["query"], "url": ""}

            # Extract from user query if this turn ran play_youtube
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
