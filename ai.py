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
)

# ── RAG Engine (the new memory backbone) ──
import rag_engine
from network_utils import is_internet_connected

logger = logging.getLogger("amigo.ai")

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


def _build_voice_prompt(query: str = "", is_voice: bool = True, has_web_context: bool = False) -> str:
    """Assemble dynamic system prompt with user profile and context."""
    now = datetime.datetime.now()
    online = is_internet_connected()
    net_status = "Connected (Online)" if online else "Disconnected (Offline)"
    prompt = (
        f"You are Amigo, a friendly and helpful AI voice assistant. "
        f"Today is {now.strftime('%A, %B %d, %Y at %I:%M %p')}.\n"
        f"Internet Status: {net_status}.\n"
    )

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
    else:
        prompt += "Current Network Status: Connected (Online). You have active internet connectivity right now.\n"

    prompt += (
        "Conversation Guidelines:\n"
        "- Answer questions, riddles, math problems, and user requests directly, accurately, and completely.\n"
        "- When following up on prior discussion, use the conversation history to maintain context.\n"
        "- Speak naturally, clearly, and concisely without repetitive greetings or robotic boilerplate.\n"
        "- Only reference remembered user facts or personal notes when the user explicitly asks about them or when directly relevant to what they are asking. NEVER blurt out or awkwardly append remembered facts to unrelated answers.\n"
        "- You are Amigo, a fully capable desktop AI assistant equipped to play music, stream audio on YouTube, launch programs, manage windows, and control system volume. NEVER state that you cannot play music, cannot access YouTube, or cannot execute actions, because the system executes these capabilities on your behalf.\n"
        "- You are the user's personal assistant; answer their questions and recall their documents and records when asked.\n"
    )
    if is_thinking_enabled():
        prompt += "- Reasoning mode: Think step-by-step inside <think> tags before providing your final answer outside of <think>.\n"
    else:
        prompt += "- Direct answer mode: Respond directly with your answer. Do NOT output <think> tags or internal deliberation.\n"

    if active_ctx := get_active_context_prompt():
        prompt += f"\n{active_ctx}"
    if user_prof := get_user_profile_prompt(query):
        prompt += f"\n{user_prof}"
    if clip := get_clipboard_text():
        prompt += f"\n[Clipboard: '{clip[:200]}']"
    if screen := get_last_screen_text():
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
            f"[Relevant Local Document Context]:\n{doc_context}\n\n"
            "Guidelines for Document Questions:\n"
            "- You have direct access to the relevant document text provided above.\n"
            "- Answer the user's question accurately using the provided document context.\n"
            "- If the user asks for a summary or key points, synthesize the main takeaways directly from the text.\n"
            "- If the user asks for specific data or facts, provide them clearly and concisely.\n"
            "- If the requested information is not mentioned in the text, state that it is not found in the provided document."
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
    temp = 0.0 if web_context else (0.1 if doc_context else 0.5)
    response = query_local_llm(messages, system_prompt=prompt, max_tokens=max_tokens, temperature=temp, thinking=thinking_active, sanitize=False)
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
    temp = 0.0 if web_context else (0.1 if doc_context else 0.6)
    token_gen = query_local_llm_stream(
        messages, system_prompt=prompt, max_tokens=max_tokens, interruption_event=interruption_event, temperature=temp, thinking=thinking_active,
    )

    for sentence in stream_sentence_chunks(token_gen, interruption_event=interruption_event):
        clean = _RE_SPEAKER_PREFIX.sub("", sentence).strip()
        if clean:
            yield clean

