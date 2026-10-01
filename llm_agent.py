"""
LLM-Based Agent for Amigo Voice Assistant.
Replaces the brittle Laya Router classifier with true natural language understanding.
Uses MiniCPM 5 2B with function calling / structured output for tool selection.
"""

import json
import logging
import os
import re
import threading
from typing import Any, Generator

from local_llm import (
    query_local_llm,
    query_local_llm_stream,
    init_local_llm,
    sanitize_for_tts,
    is_thinking_enabled,
)
from rag_engine import get_recent_conversations, get_user_profile_prompt, get_active_context_prompt, get_active_state
from tool_registry import execute_tool
from network_utils import is_internet_connected

logger = logging.getLogger("amigo.llm_agent")

# Laya model paths (for is_laya_ready check)
LAYA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "models", "laya"))
WEIGHTS_PATH = os.path.join(LAYA_DIR, "model.safetensors")
CONFIG_PATH = os.path.join(LAYA_DIR, "rl_agent_config.json")

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
        "description": "Play a song, video, or music on YouTube.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search and play on YouTube"}
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
    {
        "name": "window_management",
        "description": "Manage window state: minimize all, maximize, restore, or switch windows.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["minimize_all", "maximize", "restore", "switch_window"], "description": "Window management action"}
            },
            "required": ["action"]
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
    else:
        recent = get_recent_conversations(10)
    history_lines = []
    for turn in recent:
        u = (turn.get("user") or "").strip()
        a = (turn.get("assistant") or "").strip()
        if u:
            history_lines.append(f"User: {u}")
        if a:
            history_lines.append(f"Assistant: {a[:200]}")
    history_block = "\n".join(history_lines) if history_lines else "No recent conversation."

    # Build tool descriptions
    tool_descriptions = []
    for tool in TOOL_DEFINITIONS:
        params = tool["parameters"]["properties"]
        param_str = ", ".join([f"{k}: {v.get('type', 'string')}" for k, v in params.items()])
        tool_descriptions.append(f"- {tool['name']}({param_str}): {tool['description']}")

    tools_block = "\n".join(tool_descriptions)

    prompt = f"""You are Amigo, a capable desktop AI assistant. Today is {now.strftime('%A, %B %d, %Y at %I:%M %p')}.
Internet Status: {net_status}.

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

IMPORTANT GUIDELINES:
1. You are a DESKTOP ASSISTANT with REAL capabilities - you CAN open apps, play music, control volume, search the web, check weather, manage files, set timers/reminders, take screenshots, etc. NEVER say you cannot do these things.
2. Use the tools naturally - if the user wants something done, call the appropriate tool. Don't just chat about it.
3. For general questions, conversations, explanations, advice, coding help - use the "chat" tool with your response.
4. For ANY action that changes system state (opening apps, playing media, setting volume, etc.) - use the specific action tool.
5. You can call MULTIPLE tools in sequence if needed (e.g., open an app then maximize it).
6. Consider conversation context - references like "it", "that", "the song", "the app" refer to recent topics.
7. If internet is offline, web_search, play_youtube, get_weather will fail - inform the user.
8. Be concise in your tool calls - the system will execute them and give you results.
9. After tool execution, you'll receive results and can continue or respond to the user.
10. When you speak to the user (in "speak" or the chat response), be fun and personable per YOUR PERSONALITY above - never robotic.

CRITICAL PRIVACY & DATA ACCESS RULES:
- You have FULL ACCESS to the user's LOCAL data through the provided tools. This includes: local documents/files (PDFs, Word, Excel, etc.), Outlook emails, Outlook calendar, and saved conversation memory.
- ALL data access is LOCAL ONLY - nothing leaves the user's machine. You are NOT sending data to any cloud service.
- When users ask about their emails, calendar, documents, or personal facts - USE THE TOOLS. Do NOT refuse or say you cannot access personal data.
- The tools (document_qa, unread_emails, get_calendar, memory_recall, find_file) are specifically designed to access LOCAL data on the user's machine.
- NEVER say "I don't have access to your personal data" or "I cannot access your emails/calendar/documents" - YOU CAN via the tools.

CRITICAL DISAMBIGUATION RULES:
- "generate/create/draft/compose application|letter|email|document|code" → generate_content (shows in panel for review)
- "write/type/input application|letter|email|document" → type_text (direct typing)
- "open/launch/start/run app" → open_app (launch software)
- "application" alone: "generate/create/draft/compose" → generate_content; "write/type/input" → type_text; "open/launch/start/run" → open_app
- "open X and write Y" → open_app(X) then type_text(Y)
- "generate X and insert it" → generate_content(X) then insert_content
- "insert it", "type it here", "paste it", "put it in" → insert_content (clipboard paste)
- type_text: omit "app" param for current window; only include if user says "in vscode", "in word", etc.
- "previous/last/back track" → prev_track; "next/skip/forward track" → next_track
- City names in get_weather MUST be capitalized (e.g., "London", "Tokyo", "New York")
- set_volume action MUST be one of: mute, volume_up, volume_down, set_volume (NOT "up", "down", "mute on/off"). "set volume to N" → action: set_volume, level: N. "turn up/down volume" → action: volume_up/volume_down. "mute/unmute" → action: mute.
- DOCUMENT QA vs MEMORY RECALL: "what is my PNR", "show my booking", "read my ticket", "flight details", "hotel reservation", "document", "file", "PDF", "receipt", "confirmation" → document_qa (searches LOCAL FILES/DOCUMENTS). "what did I tell you", "remember my name", "my preference", "I told you", "recall that" → memory_recall (recalls SAVED PERSONAL FACTS).

Think about what the user ACTUALLY wants, not just keywords. Understand the INTENT behind their words.
"""

    if is_thinking_enabled():
        prompt += "\nReasoning mode: Think step-by-step inside tags before providing your final answer outside of ."
    else:
        prompt += "\nDirect answer mode: Respond directly with your answer. Do NOT output  tags or internal deliberation."

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
    """Parse tool calls from LLM response. Supports JSON tool calls format."""
    tool_calls = []

    # Try to find JSON tool calls in the response
    # Format: {"tool": "tool_name", "params": {...}}
    import re
    
    # More robust pattern that handles nested braces
    # Find all {...} blocks that contain "tool":
    json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*"tool"\s*:\s*"[^"]+"(?:[^{}]|(?:\{[^{}]*\}))*\}'
    matches = re.findall(json_pattern, response)

    for match in matches:
        try:
            call = json.loads(match)
            if "tool" in call:
                tool_calls.append({
                    "tool": call["tool"],
                    "params": call.get("params", {}),
                    "speak": call.get("speak", "")
                })
        except json.JSONDecodeError:
            continue

    # Also try to find function calling format: {"name": "tool", "arguments": {...}}
    func_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*"name"\s*:\s*"[^"]+"(?:[^{}]|(?:\{[^{}]*\}))*"arguments"\s*:\s*\{(?:[^{}]|(?:\{[^{}]*\}))*\}(?:[^{}]|(?:\{[^{}]*\}))*\}'
    matches = re.findall(func_pattern, response)
    for match in matches:
        try:
            call = json.loads(match)
            if "name" in call:
                tool_calls.append({
                    "tool": call["name"],
                    "params": call.get("arguments", {}),
                    "speak": ""
                })
        except json.JSONDecodeError:
            continue

    return tool_calls


def get_agent_actions(query: str, conversation_history: list | None = None) -> list[dict]:
    """
    Main entry point: Get agent actions for a user query using LLM reasoning.
    Returns a list of tool actions to execute.
    The LLM will decide whether to use one or multiple tools based on the user's intent.
    """
    if not initialize_agent():
        return [{"tool": "chat", "params": {}, "speak": "I'm having trouble initializing my AI engine. Please try again."}]

    # Build system prompt with full context (without conversation history - we'll pass it as messages)
    system_prompt = build_agent_system_prompt(query, conversation_history=None)

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
    
    # Add current query
    messages.append({"role": "user", "content": query})

    # Build concise tool schema for the prompt
    tool_schema_lines = []
    for tool in TOOL_DEFINITIONS:
        name = tool["name"]
        desc = tool["description"]
        props = tool["parameters"]["properties"]
        required = tool["parameters"].get("required", [])
        
        if not props:
            tool_schema_lines.append(f'- {name}: {desc}')
        else:
            param_details = []
            for param_name, param_info in props.items():
                ptype = param_info.get("type", "string")
                req = " (req)" if param_name in required else ""
                param_details.append(f"{param_name}: {ptype}{req}")
            tool_schema_lines.append(f'- {name}: {desc} | Params: {", ".join(param_details)}')

    tool_schema_block = "\n".join(tool_schema_lines)

    # Tool calling instructions appended to the user message
    tool_prompt = f"""

You have access to the following tools. When the user asks you to do something, you MUST decide which tool(s) to use and call them with the appropriate parameters.

TOOL SCHEMAS (use EXACTLY these parameters):
{tool_schema_block}

CRITICAL RULES:
1. Only use tools from the list above
2. Only use parameters defined in each tool's schema
3. For enum parameters, ONLY use the exact values listed
4. Do NOT add extra parameters not in the schema
5. Do NOT include parameters for tools that have no parameters (empty object {{}})
6. For MULTIPLE actions, output MULTIPLE JSON objects, ONE PER LINE:
   {{"tool": "tool1", "params": {{...}}, "speak": "..."}}
   {{"tool": "tool2", "params": {{...}}, "speak": "..."}}
7. After tool calls, provide your final response to the user

EXAMPLE - Single action:
{{"tool": "open_app", "params": {{"name": "vscode"}}, "speak": "Ooh, VS Code coming right up!"}}

EXAMPLE - Multiple actions (MUST be on separate lines):
{{"tool": "close_app", "params": {{"name": "chrome"}}, "speak": "Bye-bye Chrome!"}}
{{"tool": "web_search", "params": {{"query": "best browser to use"}}, "speak": "Alright, digging into the great browser debate..."}}

EXAMPLE - Generate then insert:
{{"tool": "generate_content", "params": {{"type": "application", "topic": "leave application"}}, "speak": "One leave application, coming right up!"}}
{{"tool": "insert_content", "params": {{"content": "..."}}, "speak": "And... pasted in! Done!"}}

EXAMPLE - Open then type:
{{"tool": "open_app", "params": {{"name": "notepad"}}, "speak": "Notepad's up, let's write!"}}
{{"tool": "type_text", "params": {{"text": "Hello world"}}, "speak": "Typing away!"}}"""

    # Append tool prompt to the last user message
    messages[-1]["content"] += tool_prompt

    try:
        response = query_local_llm(
            messages,
            system_prompt="",  # System prompt is already in messages
            max_tokens=1024,
            temperature=0.6,
            thinking=is_thinking_enabled(),
            sanitize=False
        )

        # Parse tool calls from response
        tool_calls = _parse_tool_calls(response)

        # Extract final conversational response (after tool calls)
        final_response = _extract_final_response(response, tool_calls)

        # If no tool calls found, default to chat
        if not tool_calls:
            # Check if it's a probe query (like "are you there")
            from local_llm import _RE_PROBE_GUARD
            if _RE_PROBE_GUARD.search(query):
                return [{"tool": "chat", "params": {}, "speak": ""}], ""
            # Otherwise treat as chat - let the LLM respond naturally with creativity enabled
            return [{"tool": "chat", "params": {}, "speak": final_response}], final_response

        # Return both tool calls and final response
        return tool_calls, final_response

    except Exception as e:
        logger.error(f"[LLM Agent] Error getting actions: {e}")
        return [{"tool": "chat", "params": {}, "speak": "I encountered an error processing your request."}], "I encountered an error processing your request."


def _extract_final_response(response: str, tool_calls: list) -> str:
    """Extract the final conversational response after tool calls."""
    # Remove tool call JSON from response
    import re
    # Remove JSON tool calls (both formats)
    cleaned = re.sub(r'\{[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', '', response)
    cleaned = re.sub(r'\{[^{}]*"name"\s*:\s*"[^"]+"[^{}]*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}', '', cleaned)
    # Remove any remaining JSON-like blocks with tool/name
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

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query}
    ]

    tool_prompt = f"""The user said: "{query}"

Analyze what they want and decide which tool(s) to use. Respond with tool calls in this format:
{{"tool": "tool_name", "params": {{"param": "value"}}, "speak": "optional confirmation message"}}

You can include multiple tool calls if needed. After the tool calls, provide your final response to the user."""

    full_prompt = system_prompt + "\n\n" + tool_prompt

    try:
        token_gen = query_local_llm_stream(
            full_prompt,
            system_prompt="",
            max_tokens=1024,
            temperature=0.6,
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
    Compatibility function matching the old laya_router.get_agent_action signature.
    This allows dropping in the new agent without changing callers.
    Returns only the tool actions (first element of tuple).
    """
    actions, _ = get_agent_actions(query, conversation_history)
    return actions


def get_agent_actions_with_response(query: str, conversation_history: list | None = None) -> tuple[list[dict], str]:
    """
    Get agent actions AND the final conversational response from the LLM.
    Returns tuple of (actions, final_response).
    """
    return get_agent_actions(query, conversation_history)


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
# Utility Functions (replacing laya_router utilities)
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


def is_laya_ready() -> bool:
    """Check if Laya weights and configuration exist locally."""
    return (
        os.path.exists(WEIGHTS_PATH)
        and os.path.getsize(WEIGHTS_PATH) > 500_000_000
        and os.path.exists(CONFIG_PATH)
    )