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
)

# ── RAG Engine (the new memory backbone) ──
import rag_engine

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


def get_user_profile_prompt() -> str:
    """Format user profile for prompt context."""
    return rag_engine.get_user_profile_prompt()


def get_active_context_prompt() -> str:
    """Format active state for prompt context."""
    return rag_engine.get_active_context_prompt()


# ═══════════════════════════════════════════════════════════════
#  LLM Prompt Building  (now RAG-enhanced)
# ═══════════════════════════════════════════════════════════════

def _build_voice_prompt(query: str = "") -> str:
    """Assemble dynamic voice system prompt with user profile and context."""
    now = datetime.datetime.now()
    prompt = (
        f"You are Amigo, a private local AI voice assistant on the user's PC. "
        f"Today is {now.strftime('%A, %B %d, %Y at %I:%M %p')}.\n"
        "You have authorized offline access to the user's local documents, files, and notes. "
        "When the user asks about their files, tickets, receipts, or data, answer directly with the exact details, codes, PNRs, dates, and facts from the document context. "
        "Respond in natural spoken English in plain sentences. No markdown, bullet points, or raw JSON. "
        "Keep answers concise and direct for voice output."
    )

    if active_ctx := get_active_context_prompt():
        prompt += f"\n{active_ctx}"
    if user_prof := get_user_profile_prompt():
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
        recent = rag_engine.get_recent_conversations(count=10)
        for c in recent:
            u = (c.get("user", "") or "").strip()
            a = (c.get("assistant", "") or "").strip()
            if u:
                messages.append({"role": "user", "content": u[:1000]})
                messages.append({"role": "assistant", "content": (a or "Done.")[:1500]})

    # Auto-fetch RAG document context if not already provided
    if not doc_context and query:
        try:
            doc_context = rag_engine.build_rag_context(query, top_k=5)
        except Exception:
            doc_context = ""

    # Build user content with any injected context
    user_parts: list[str] = []
    if web_context:
        user_parts.append(f"[Web Search Facts]:\n{web_context}")
    if doc_context:
        user_parts.append(f"[Relevant Local Document Context]:\n{doc_context}\nAnswer the question directly using the details from these documents.")
    user_parts.append(query)

    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    return messages


# ═══════════════════════════════════════════════════════════════
#  LLM Response Generation
# ═══════════════════════════════════════════════════════════════

def get_ai_response(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
) -> str:
    """Query local AI model with RAG-enhanced context and return plain speech text."""
    if not query or not query.strip():
        return "How can I help you today?"

    messages = _build_ai_messages(query, use_memory=use_memory, web_context=web_context, doc_context=doc_context)
    prompt = _build_voice_prompt(query=query)
    response = query_local_llm(messages, system_prompt=prompt, max_tokens=512)
    response = re.sub(r"^(Amigo|Assistant|AI):\s*", "", response, flags=re.I).strip()
    return sanitize_for_tts(response) or "I am here and ready to help."


def get_ai_response_stream(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    doc_context: str = "",
    interruption_event: threading.Event | None = None,
) -> Generator[str, None, None]:
    """Stream sentence chunks from local AI model with RAG-enhanced context."""
    if not query or not query.strip():
        yield "How can I help you today?"
        return

    messages = _build_ai_messages(query, use_memory=use_memory, web_context=web_context, doc_context=doc_context)
    prompt = _build_voice_prompt(query=query)
    token_gen = query_local_llm_stream(
        messages, system_prompt=prompt, max_tokens=512, interruption_event=interruption_event,
    )

    for sentence in stream_sentence_chunks(token_gen, interruption_event=interruption_event):
        clean = re.sub(r"^(Amigo|Assistant|AI):\s*", "", sentence, flags=re.I).strip()
        if clean:
            yield clean
