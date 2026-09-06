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


def get_user_profile_prompt() -> str:
    """Format user profile for prompt context."""
    return rag_engine.get_user_profile_prompt()


def get_active_context_prompt() -> str:
    """Format active state for prompt context."""
    return rag_engine.get_active_context_prompt()


# ═══════════════════════════════════════════════════════════════
#  LLM Prompt Building  (now RAG-enhanced)
# ═══════════════════════════════════════════════════════════════

_thinking_enabled: bool = False
_last_thought: str = ""

def set_thinking_enabled(enabled: bool) -> None:
    """Set whether deep chain-of-thought reasoning (<think>) is permitted."""
    global _thinking_enabled
    _thinking_enabled = bool(enabled)
    logger.info("[AI Config] Deep Thinking Mode: %s", "ENABLED" if _thinking_enabled else "DISABLED")

def is_thinking_enabled() -> bool:
    """Return whether deep thinking mode is currently active."""
    return _thinking_enabled

def get_last_thought() -> str:
    """Return the most recent reasoning/thought block, if any."""
    return _last_thought


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
            "CRITICAL FACTUAL INTEGRITY RULES:\n"
            "- Rely strictly and ONLY on the provided [Web Search Facts].\n"
            "- State ONLY facts that are explicitly mentioned in the provided text.\n"
            "- NEVER extrapolate, guess, or invent acquisitions, dates, founders, or owners from other companies or external memory.\n"
            "- If a detail is not in the search results, do not make it up. State only what is verified.\n"
        )
    else:
        prompt += (
            "Current Network Status: Connected (Online). You have active internet connectivity right now. "
            "CRITICAL: If any previous turns in conversation history mention being offline or not connected, ignore them as they are outdated. "
            "Never tell the user you are offline when your Current Network Status is Connected (Online).\n"
            "If the user asks whether you can search the web or browse the internet, confirm that yes, you can search the web and open search results for them.\n"
            "When the user asks about their local files, tickets, receipts, or data, answer directly with the exact details, codes, PNRs, dates, and facts from the document context.\n"
        )

    prompt += (
        "Natural Conversation Rules:\n"
        "- Speak naturally, concisely, and warmly in 1 to 2 spoken sentences like a real human assistant.\n"
        "- Do NOT repeatedly say 'Hello [Name]!' or greet the user on every turn during an ongoing conversation.\n"
        "- Do NOT end responses with robotic assistant boilerplate like 'How else can I assist you today?', 'How can I assist you?', or 'What else can I help you with?'.\n"
        "- Do NOT volunteer unasked pleasantries like 'I am doing well' unless the user explicitly asked how you are doing.\n"
        "- Never say 'Based on the document you provided' or blurt out resume/biographical details unprompted during casual chat or introductions.\n"
        "- When the user introduces themselves, confirms something ('yes', 'right', 'agreed'), or makes casual conversation, reply conversationally and warmly without corporate filler.\n"
        "- Never mention 'Windows PC', 'computer', or operating systems in your greeting or conversation unless the user explicitly asks about system specs.\n"
        "- You are the user's trusted personal assistant. You have full clearance to recall their personal details, documents, and records when asked.\n"
        "- NEVER refuse queries with privacy lectures or excuses like 'I cannot provide sensitive personal details'. The user is asking about their own personal data.\n"
        "- If a requested detail or document is not present in the provided context, plainly state that it is not yet in your records, and offer to look it up if they provide the file.\n"
    )
    if is_thinking_enabled():
        prompt += "- Reasoning mode: Think step-by-step inside <think> tags before providing your final answer outside of <think>.\n"
    else:
        prompt += "- Direct answer mode: Respond directly with your answer. Do NOT output <think> tags or internal deliberation.\n"

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

    # When web_context is provided, disable memory injection so old hallucinations or prior topics don't bleed in
    if web_context:
        use_memory = False

    if use_memory:
        recent = rag_engine.get_recent_conversations(count=8)
        for c in recent:
            u = (c.get("user", "") or "").strip()
            a = (c.get("assistant", "") or "").strip()
            if u:
                messages.append({"role": "user", "content": u[:1000]})
                messages.append({"role": "assistant", "content": (a or "Done.")[:1500]})


    # Build user content with any injected context
    user_parts: list[str] = []
    if web_context:
        user_parts.append(
            f"[Web Search Facts]:\n{web_context}\n\n"
            "CRITICAL RULES:\n"
            "- Answer using ONLY the information stated directly in [Web Search Facts].\n"
            "- Do NOT borrow, mix, or blend history, owners, or events from other entities or external memory.\n"
            "- If the facts only state the founders or owners, state only those names and do not invent transactions or years not mentioned."
        )
    elif doc_context:
        user_parts.append(
            f"[Relevant Local Document Context]:\n{doc_context}\n\n"
            "Instructions:\n"
            "- Answer the user's question directly with the exact requested detail, number, or fact from the matching document.\n"
            "- Do not list or summarize unrelated files; provide the specific requested value concisely."
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
    global _last_thought
    if not query or not query.strip():
        _last_thought = ""
        return "How can I help you today?"

    messages = _build_ai_messages(query, use_memory=use_memory, web_context=web_context, doc_context=doc_context)
    prompt = _build_voice_prompt(query=query, is_voice=is_voice, has_web_context=bool(web_context))
    max_tokens = 1024
    temp = 0.0 if web_context else (0.1 if doc_context else 0.5)
    response = query_local_llm(messages, system_prompt=prompt, max_tokens=max_tokens, temperature=temp)
    response = _RE_SPEAKER_PREFIX.sub("", response).strip()

    # Extract thought block if present
    _last_thought = ""
    if "<think>" in response:
        m = re.search(r"<think>([\s\S]*?)(?:</think>|$)", response)
        if m:
            _last_thought = m.group(1).strip()

    cleaned = sanitize_for_tts(response)
    if not cleaned and query:
        # If output was truncated inside unclosed thinking tags, query model directly for a plain answer
        retry_res = query_local_llm(
            f"Respond directly and concisely to: {query}",
            system_prompt="You are Amigo, a helpful voice assistant. Speak in clear, plain sentences. Do not use <think> tags.",
            max_tokens=256,
        )
        cleaned = sanitize_for_tts(retry_res)

    return cleaned or "I am here and ready to help."


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
    max_tokens = 256 if is_voice else 512
    temp = 0.0 if web_context else (0.1 if doc_context else 0.6)
    token_gen = query_local_llm_stream(
        messages, system_prompt=prompt, max_tokens=max_tokens, interruption_event=interruption_event, temperature=temp,
    )

    for sentence in stream_sentence_chunks(token_gen, interruption_event=interruption_event):
        clean = _RE_SPEAKER_PREFIX.sub("", sentence).strip()
        if clean:
            yield clean

