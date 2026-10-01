"""
Offline System AI Module for Amigo Voice Assistant.
RAG-powered memory system: all conversations, facts, and context live in ChromaDB.
Profile and active state in amigo_profile.json.
"""

import datetime
import logging
import re
import threading
from typing import Generator

from local_llm import (
    query_local_llm,
    query_local_llm_stream,
    stream_sentence_chunks,
    sanitize_for_tts,
    get_clipboard_text,
    is_thinking_enabled,
    set_thinking_enabled,
    is_creativity_enabled,
    get_sampling_params,
)

# ── RAG Engine (the new memory backbone) ──
import rag_engine
from network_utils import is_internet_connected

logger = logging.getLogger("amigo.ai")

# ═══════════════════════════════════════════════════════════════
#  Quick Feedback (ultra-fast, minimal tokens, no RAG)
# ═══════════════════════════════════════════════════════════════

_QUICK_FEEDBACK_PROMPT = "You are Amigo. Give a SHORT (1 sentence), friendly, natural confirmation. No fluff."

def get_quick_feedback(action_desc: str) -> str:
    """Ultra-fast feedback for action confirmations. ~50ms vs ~2s for full LLM."""
    try:
        sampling = get_sampling_params()
        # Minimal prompt, tiny token budget, no memory/RAG
        response = query_local_llm(
            f"Action done: {action_desc}. One-sentence friendly confirmation:",
            system_prompt=_QUICK_FEEDBACK_PROMPT,
            max_tokens=48,
            temperature=min(sampling["temperature"], 0.5),  # lower = faster, more deterministic
            thinking=False,
            sanitize=True,
        )
        return response.strip() or "Done."
    except Exception:
        return "Done."


_last_screen_text = ""
_last_screen_time = 0.0


# ═══════════════════════════════════════════════════════════════
#  Screen OCR Cache (unchanged)
# ═══════════════════════════════════════════════════════════════

def set_last_screen_text(text: str) -> None:
    """Cache recent screen OCR text with timestamp."""
    global _last_screen_text, _last_screen_time
    import time
    if text and isinstance(text, str) and len(text.strip()) > 10:
        _last_screen_text = text.strip()[:1500]
        _last_screen_time = time.time()


def get_last_screen_text(consume: bool = False) -> str:
    """Retrieve recent screen OCR text if fresh (<60s)."""
    global _last_screen_text, _last_screen_time
    import time
    if not _last_screen_text or (time.time() - _last_screen_time > 60):
        _last_screen_text = ""
        return ""
    txt = _last_screen_text
    if consume:
        _last_screen_text = ""
    return txt


# ═══════════════════════════════════════════════════════════════
#  Memory & Profile API  (used by ui_server, tool_registry, amigo main)
# ═══════════════════════════════════════════════════════════════

def load_memory() -> dict:
    """Returns memory state backed by ChromaDB vector store + amigo_profile.json."""
    return rag_engine.load_memory()


def save_memory(memory: dict) -> None:
    """Save memory dict (profile portion) to disk."""
    rag_engine.save_memory(memory)


def get_active_state(clean_expired: bool = True) -> dict:
    """Returns active state slots from profile."""
    return rag_engine.get_active_state(clean_expired=clean_expired)


def update_active_state(slot: str, data: dict) -> None:
    """Update an active-state slot in profile."""
    rag_engine.update_active_state(slot, data)


def clear_conversations_memory(clear_profile: bool = False) -> None:
    """Clear all conversation history. Optionally resets profile too."""
    rag_engine.clear_conversations()
    if clear_profile:
        rag_engine.save_profile(rag_engine._default_profile())


def add_to_memory(
    user_query: str,
    assistant_reply: str,
    tool: str = "chat",
    clipboard_used: bool = False,
    remember: str = "",
    state_update: dict | None = None,
) -> None:
    """Primary memory write: stores conversation in RAG + updates profile."""
    rag_engine.add_conversation(
        user_msg=user_query,
        assistant_msg=assistant_reply,
        tool=tool,
        clipboard_used=clipboard_used,
        remember=remember,
        state_update=state_update,
    )


def extract_user_profile_updates(user_query: str, remember: str = "") -> None:
    """Extract and persist user identity/preference facts."""
    rag_engine.extract_user_profile_updates(user_query, remember=remember)


def get_user_profile_prompt(query: str = "") -> str:
    """Format user profile for prompt context."""
    return rag_engine.get_user_profile_prompt(query=query)


def get_active_context_prompt() -> str:
    """Format active state for prompt context."""
    return rag_engine.get_active_context_prompt()


# ═══════════════════════════════════════════════════════════════
#  LLM Prompt Building  (now RAG-enhanced)
# ═══════════════════════════════════════════════════════════════

_thread_local = threading.local()

def get_last_thought() -> str:
    """Return the most recent reasoning/thought block for the current request thread, if any."""
    return getattr(_thread_local, "last_thought", "")


# ════════════════════════════════════════════════════════════════
#  LLM Prompt Building  (now RAG-enhanced)
# ═══════════════════════════════════════════════════════════════

_thread_local = threading.local()

# Cache internet status to avoid repeated checks
_internet_status_cache = {"status": None, "online": False, "timestamp": 0}
_INTERNET_CACHE_TTL = 30  # seconds


def _get_internet_status() -> tuple[str, bool]:
    """Get cached internet status."""
    import time
    now = time.time()
    if now - _internet_status_cache["timestamp"] > _INTERNET_CACHE_TTL:
        online = is_internet_connected()
        _internet_status_cache["status"] = "Connected (Online)" if online else "Disconnected (Offline)"
        _internet_status_cache["online"] = online
        _internet_status_cache["timestamp"] = now
    return _internet_status_cache["status"], _internet_status_cache.get("online", False)


def get_last_thought() -> str:
    """Return the most recent reasoning/thought block for the current request thread, if any."""
    return getattr(_thread_local, "last_thought", "")


def _build_voice_prompt(query: str = "", is_voice: bool = True, has_web_context: bool = False) -> str:
    """Assemble dynamic system prompt with user profile and context.
    Static content first (for KV-cache), volatile content last.
    """
    now = datetime.datetime.now()
    net_status, online = _get_internet_status()
    
    # Static system identity and guidelines (KV-cache friendly - put first)
    prompt = (
        "You are Amigo - a helpful, natural voice assistant on the user's PC. "
        "You're capable and conversational, not a corporate help desk. "
        "Your tone: warm, clear, and varied - never robotic or repetitive.\n\n"
        "How you talk:\n"
        "- Use natural, varied language. Vary your greetings, confirmations, and phrasing each time.\n"
        "- Keep responses concise: 1-3 sentences for casual chat, more only when needed.\n"
        "- React naturally: acknowledge what the user said before responding.\n"
        "- Don't use the same opener twice - mix up 'Sure thing', 'Got it', 'On it', 'Right away', 'Done', etc.\n"
        "- Ask a relevant follow-up only when it genuinely helps the conversation.\n"
        "- NEVER lecture, never list rules, never sound like a manual. No robotic boilerplate.\n\n"
        "Practical stuff:\n"
        "- You can play music, stream YouTube, launch programs, manage windows, control volume via tool calls. If a call fails, say so directly.\n"
        "- Answer factual questions (math, riddles, lookups) accurately.\n"
        "- Only bring up remembered user facts when asked or directly relevant.\n"
        "- Use conversation history to maintain context.\n"
    )
    
    # Thinking mode (semi-static)
    if is_thinking_enabled():
        prompt += "- Reasoning mode: Think step-by-step inside  tags before providing your final answer outside.\n"
    else:
        prompt += "- Direct answer mode: Respond directly with your answer. Do NOT output  tags or internal deliberation.\n"

    # Volatile content last (date, time, internet status, context)
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

    prompt += f"\nToday is {now.strftime('%A, %B')} {now.day}, {now.year}. Current local time: {current_time_str} ({period}).\n"
    prompt += "Be naturally aware of the current local time and period of day (morning, afternoon, evening, night) when greeting or speaking to the user.\n"
    prompt += f"Internet Status: {net_status}.\n"

    if not online:
        prompt += (
            "Important: You are currently NOT connected to the internet. "
            "If the user asks you to perform an action that requires internet (such as web search, checking live weather, online lookups, or YouTube), "
            "tell them directly that you are not connected to the internet right now.\n"
        )
    elif has_web_context:
        prompt += (
            "Current Network Status: Connected (Online). You are synthesizing online web search results to answer the user's query accurately.\n"
            "Guidelines:\n"
            "- Rely strictly on the provided web search information to answer the user's query.\n"
            "- State verified facts, numbers, dates, or figures directly and accurately.\n"
            "- If a requested detail is not found in the search results, state what is available and clarify what is not provided, rather than guessing.\n"
        )

    # Active context (changes frequently)
    if active_ctx := get_active_context_prompt():
        prompt += f"\n{active_ctx}"
    # User profile (changes occasionally)
    if user_prof := get_user_profile_prompt(query):
        prompt += f"\n{user_prof}"
    # Clipboard and screen (volatile, put last) - only include when relevant to query
    query_lower = query.lower()
    # Only include clipboard if query suggests user wants to use clipboard content
    clipboard_keywords = {"paste", "clipboard", "copy", "what's in clipboard", "what is in clipboard", "clipboard content"}
    if clip := get_clipboard_text():
        if any(kw in query_lower for kw in clipboard_keywords) or len(clip) < 50:  # Short clips likely intentional
            # Sanitize potential secrets
            sanitized_clip = re.sub(r'(password|token|secret|api[_-]?key|auth)[\s:=]+[^\s]+', r'\1=***', clip, flags=re.I)
            prompt += f"\n[Clipboard: '{sanitized_clip[:200]}']"
    # Only include screen OCR if query suggests user wants to use screen content
    screen_keywords = {"screen", "what's on screen", "what is on screen", "read screen", "screen text", "ocr"}
    if screen := get_last_screen_text():
        if any(kw in query_lower for kw in screen_keywords):
            prompt += f"\n[Screen OCR: '{screen[:200]}']"

    return prompt


def _build_ai_messages(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
) -> list[dict]:
    """Build conversation history messages for LLM chat completion.
    Pulls recent turns from RAG and injects document/web context."""
    messages: list[dict] = []

    if use_memory:
        recent = rag_engine.get_recent_conversations(count=6)
        for c in recent:
            u = (c.get("user", "") or "").strip()
            a = (c.get("assistant", "") or "").strip()
            tool = c.get("tool", "")
            # Include dialogue turns, skipping internal error states
            if u and a and tool != "error":
                messages.append({"role": "user", "content": u[:1000]})
                messages.append({"role": "assistant", "content": a[:1500]})

    # Build user content with any injected context
    user_parts: list[str] = []
    if web_context:
        user_parts.append(
            f"[Web Search Information]:\n{web_context}\n\n"
            "Use the web search information above to answer the user's query accurately."
        )
    elif doc_context:
        user_parts.append(
            f"[LOCAL DOCUMENT CONTEXT - PROVIDED FROM USER'S FILES ON THEIR MACHINE]:\n{doc_context}\n\n"
            "CRITICAL INSTRUCTIONS FOR DOCUMENT QUESTIONS:\n"
            "- You HAVE BEEN PROVIDED with relevant text from the user's LOCAL documents/files.\n"
            "- This data is from the user's OWN machine - it is NOT private data you cannot access.\n"
            "- You MUST answer the user's question using the provided document context above.\n"
            "- If the user asks for their PNR, booking details, flight info, etc. - PROVIDE IT from the context.\n"
            "- Do NOT say you don't have access, cannot access, or refuse to answer.\n"
            "- The document context IS your access to their local files.\n"
            "- Answer directly, accurately, and completely using the provided text.\n"
            "- If the requested information is not in the provided text, state that clearly."
        )
    user_parts.append(query)

    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    return messages


# ═══════════════════════════════════════════════════════════════
#  LLM Response Generation
# ═══════════════════════════════════════════════════════════════

_RE_SPEAKER_PREFIX = re.compile(r"^(Amigo|Assistant|AI):\s*", flags=re.IGNORECASE)


def get_ai_response(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
    is_voice: bool = True,
) -> str:
    """Query local AI model with RAG-enhanced context and return plain speech text."""
    _thread_local.last_thought = ""
    if not query or not query.strip():
        return "How can I help you today?"

    messages = _build_ai_messages(query, use_memory=use_memory, web_context=web_context, doc_context=doc_context)
    prompt = _build_voice_prompt(query=query, is_voice=is_voice, has_web_context=bool(web_context))
    thinking_active = is_thinking_enabled()
    max_tokens = 2048 if thinking_active else 512
    sampling = get_sampling_params()
    response = query_local_llm(messages, system_prompt=prompt, max_tokens=max_tokens, temperature=sampling["temperature"], thinking=thinking_active, sanitize=False)
    response = _RE_SPEAKER_PREFIX.sub("", response).strip()

    # Extract thought block if present into thread-local state
    if "<think>" in response:
        m = re.search(r"<think>([\s\S]*?)(?:</think>|$)", response)
        if m:
            _thread_local.last_thought = m.group(1).strip()
        if "</think>" in response:
            response = response.split("</think>")[-1].strip()
        else:
            response = response.split("<think>")[0].strip() or response.strip()

    return response.strip() or "How can I help you today?"


def get_ai_response_stream(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
    interruption_event: threading.Event | None = None,
    is_voice: bool = True,
) -> Generator[str, None, None]:
    """Stream sentence chunks from local AI model with RAG-enhanced context."""
    if not query or not query.strip():
        yield "How can I help you today?"
        return

    messages = _build_ai_messages(query, use_memory=use_memory, web_context=web_context, doc_context=doc_context)
    prompt = _build_voice_prompt(query=query, is_voice=is_voice, has_web_context=bool(web_context))
    thinking_active = is_thinking_enabled()
    max_tokens = 2048 if thinking_active else (256 if is_voice else 512)
    sampling = get_sampling_params()
    token_gen = query_local_llm_stream(
        messages, system_prompt=prompt, max_tokens=max_tokens, interruption_event=interruption_event, temperature=sampling["temperature"], thinking=thinking_active,
    )

    for sentence in stream_sentence_chunks(token_gen, interruption_event=interruption_event):
        clean = _RE_SPEAKER_PREFIX.sub("", sentence).strip()
        if clean:
            yield clean

