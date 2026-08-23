"""
Offline System AI Module for Amigo Voice Assistant.
Powered by an offline Local LLM running directly on your system. Zero API keys.

Memory Architecture — Architecture 1: Structured State & Context-Aware Working Memory:
  Tier 1 (Active Working State) : Slot-based real-time state (media, apps, web searches, active subjects) with TTL expiry
  Tier 2 (Episodic Buffer)      : Sliding multi-turn conversation window (last 10 turns in prompt, 50 on disk)
  Tier 3 (User Profile & Facts) : Structured identity, user preferences, habits, and domain-categorized persistent facts
  Tier 4 (Mirror Memory)        : Passive interaction style & cadence tracking
"""

import datetime
import json
import logging
import os
import re
import threading
from collections import Counter
from typing import Any

from local_llm import sanitize_for_tts, query_local_llm

logger = logging.getLogger("amigo.ai")

MEMORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "amigo_memory.json"
)

# ---------------------------------------------------------------------------
# Zero-Latency RAM Memory Cache
# Stays 100% resident in Python RAM for <0.1ms instant access.
# Disk I/O runs asynchronously on background threads to prevent voice lag.
# ---------------------------------------------------------------------------
_memory_cache = None
_save_lock = threading.Lock()
_last_screen_text = ""
_last_screen_time = 0.0

# State Time-To-Live (TTL) in seconds
MEDIA_STATE_TTL_SECONDS = 1800   # 30 mins
APP_STATE_TTL_SECONDS   = 1800   # 30 mins
SEARCH_STATE_TTL_SECONDS = 900   # 15 mins


def set_last_screen_text(text: str) -> None:
    """Cache the most recent screen OCR text with timestamp."""
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


def clear_last_screen_text() -> None:
    """Explicitly clear screen cache."""
    global _last_screen_text, _last_screen_time
    _last_screen_text = ""
    _last_screen_time = 0.0


# ---------------------------------------------------------------------------
# Memory Initialization & Schema Migration
# ---------------------------------------------------------------------------

def _migrate_memory_schema(mem: dict) -> dict:
    """
    Ensure memory dict adheres to the Architecture 1 schema.
    Migrates legacy schemas without data loss.
    """
    # 1. Tier 1: Active Working State
    active_state = mem.setdefault("active_state", {})
    active_state.setdefault("current_media", {"title": "", "artist": "", "platform": "", "query": "", "timestamp": None})
    active_state.setdefault("active_app", {"name": "", "timestamp": None})
    active_state.setdefault("last_search", {"query": "", "summary": "", "timestamp": None})
    active_state.setdefault("active_subject", {"name": "", "category": "", "timestamp": None})

    # 2. Tier 2: Episodic conversation history
    mem.setdefault("conversations", [])

    # 3. Tier 3: Structured User Profile
    user_profile = mem.setdefault("user_profile", {})
    if not isinstance(user_profile, dict):
        user_profile = {}
        mem["user_profile"] = user_profile

    user_profile.setdefault("identity", {"name": "", "role": ""})
    user_profile.setdefault("preferences", {
        "favorite_artists": [],
        "favorite_genres": [],
        "favorite_city": "",
        "theme": "dark",
        "preferred_style": "conversational",
    })
    user_profile.setdefault("habits", {
        "active_time": "",
        "communication_style": "",
    })
    custom_facts = user_profile.setdefault("custom_facts", [])

    # Backward compatibility: migrate legacy user_facts
    legacy_facts = mem.get("user_facts", [])
    if isinstance(legacy_facts, list):
        for f in legacy_facts:
            if isinstance(f, str) and f.strip() and f.strip() not in custom_facts:
                custom_facts.append(f.strip())

    # 4. Tier 4: Mirror Memory (Interaction Style)
    mem.setdefault("interaction_style", {
        "total_turns":    0,
        "session_count":  0,
        "last_seen":      None,
        "active_hours":   [],
        "avg_query_length": 0,
        "clipboard_uses": 0,
        "screen_reads":   0,
        "top_tools":      {},
        "top_topics":     [],
    })

    return mem


def load_memory() -> dict:
    """Instant 0.1ms RAM lookup for memory cache with full schema guarantees."""
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache

    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    _memory_cache = _migrate_memory_schema(loaded)
        except Exception as e:
            logger.debug(f"Memory read exception: {e}")

    if not isinstance(_memory_cache, dict):
        _memory_cache = _migrate_memory_schema({})

    return _memory_cache


def _async_disk_save(data_copy: dict) -> None:
    """Background thread worker for disk persistence without blocking audio loop."""
    with _save_lock:
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data_copy, f, indent=2)
        except Exception as e:
            logger.error(f"Error persisting memory async: {e}")


def save_memory(memory: dict) -> None:
    """Update RAM cache immediately and queue async disk save (capped to 50 turns)."""
    global _memory_cache
    if "conversations" in memory and isinstance(memory["conversations"], list):
        memory["conversations"] = memory["conversations"][-50:]
    _memory_cache = memory

    # Copy and save in background thread so audio/voice never lags
    data_copy = json.loads(json.dumps(memory))
    threading.Thread(target=_async_disk_save, args=(data_copy,), daemon=True).start()


def clear_conversations_memory(clear_profile: bool = False) -> None:
    """Clear conversation history, reset active slots, and persist immediately."""
    global _memory_cache
    memory = load_memory()
    memory["conversations"] = []
    memory["active_state"] = {
        "current_media": None,
        "active_app": {"name": "", "timestamp": None},
        "last_search": {"query": "", "summary": "", "timestamp": None},
        "active_subject": {"name": "", "timestamp": None},
    }
    if clear_profile:
        memory["user_profile"] = {
            "identity": {"name": "", "role": ""},
            "preferences": {
                "favorite_artists": [],
                "favorite_genres": [],
                "favorite_city": "",
                "theme": "dark",
                "preferred_style": "conversational",
            },
            "custom_facts": [],
        }
        memory["interaction_style"] = {
            "total_turns": 0,
            "session_count": 0,
            "last_seen": None,
            "active_hours": [],
            "avg_query_length": 0,
            "clipboard_uses": 0,
            "screen_reads": 0,
            "top_tools": {},
            "top_topics": [],
        }
    _memory_cache = memory
    with _save_lock:
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(memory, f, indent=2)
        except Exception as e:
            logger.error(f"Error persisting cleared memory: {e}")


# ---------------------------------------------------------------------------
# Tier 1 — Active Working State Management (Slots + TTL Expiry)
# ---------------------------------------------------------------------------

def _is_expired(timestamp_str: str | None, ttl_seconds: int) -> bool:
    """Check if an ISO timestamp has passed its TTL."""
    if not timestamp_str:
        return True
    try:
        ts = datetime.datetime.fromisoformat(timestamp_str)
        return (datetime.datetime.now() - ts).total_seconds() > ttl_seconds
    except Exception:
        return True


def get_active_state(clean_expired: bool = True) -> dict:
    """Retrieve the current active state, optionally clearing expired slots."""
    memory = load_memory()
    active_state = memory.get("active_state", {})

    if clean_expired:
        # Media TTL
        curr_media = active_state.get("current_media", {})
        if curr_media and _is_expired(curr_media.get("timestamp"), MEDIA_STATE_TTL_SECONDS):
            active_state["current_media"] = {"title": "", "artist": "", "platform": "", "query": "", "timestamp": None}

        # App TTL
        curr_app = active_state.get("active_app", {})
        if curr_app and _is_expired(curr_app.get("timestamp"), APP_STATE_TTL_SECONDS):
            active_state["active_app"] = {"name": "", "timestamp": None}

        # Search TTL
        curr_search = active_state.get("last_search", {})
        if curr_search and _is_expired(curr_search.get("timestamp"), SEARCH_STATE_TTL_SECONDS):
            active_state["last_search"] = {"query": "", "summary": "", "timestamp": None}

    return active_state


def update_active_state(slot: str, data: dict) -> None:
    """
    Atomically update an active state slot with current timestamp.
    Slots: 'current_media', 'active_app', 'last_search', 'active_subject'.
    """
    if not slot or not isinstance(data, dict):
        return

    memory = load_memory()
    active_state = memory.setdefault("active_state", {})
    existing = active_state.setdefault(slot, {})

    payload = dict(data)
    payload["timestamp"] = datetime.datetime.now().isoformat()
    existing.update(payload)
    active_state[slot] = existing
    save_memory(memory)


def get_active_context_prompt() -> str:
    """
    Format active state into a concise string for LLM prompts.
    Zero ambiguity for follow-up commands like 'download that music' or 'close it'.
    """
    state = get_active_state(clean_expired=True)
    parts = []

    media = state.get("current_media", {})
    if media and (media.get("title") or media.get("query")):
        title = media.get("title") or media.get("query")
        artist = media.get("artist", "")
        plat = media.get("platform", "YouTube")
        if artist:
            parts.append(f"Playing '{title}' by '{artist}' on {plat}")
        else:
            parts.append(f"Playing '{title}' on {plat}")

    app = state.get("active_app", {})
    if app and app.get("name"):
        parts.append(f"Active App: {app['name']}")

    search = state.get("last_search", {})
    if search and search.get("query"):
        parts.append(f"Recent Search: '{search['query']}'")

    subject = state.get("active_subject", {})
    if subject and subject.get("name"):
        parts.append(f"Current Topic: {subject['name']}")

    if not parts:
        return ""
    return "[Active System State: " + " | ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Tier 3 — Structured User Profile & Dynamic Open-Ended Extraction
# Fully generalized for any user in the world — zero hardcoded lists or bias.
# ---------------------------------------------------------------------------

_RE_NAME_EXTRACT = re.compile(
    r"\b(?:my name is|call me|myself)\s+([A-Za-z\s\.\-]{2,40})(?:\.|$|,|\s+(?:and|with|but|who|i am|i'm)\b)",
    re.IGNORECASE,
)
_RE_FAV_ARTIST = re.compile(
    r"\b(?:my (?:favorite|favourite) (?:artist|singer|band|musician|creator) is|i (?:really )?(?:love|like) (?:listening to|the band|the artist))\s+([A-Za-z0-9\s\.\-_&]{2,50}?)(?:\.|$|,|\s+(?:and|with|but|who)\b)",
    re.IGNORECASE,
)
_RE_FAV_GENRE = re.compile(
    r"\b(?:my (?:favorite|favourite) (?:music (?:genre|style)?|genre) is|i (?:love|like|enjoy) (?:listening to )?([a-zA-Z\s\-]+?)\s+(?:music|songs))\b",
    re.IGNORECASE,
)
_RE_CITY_EXTRACT = re.compile(
    r"\b(?:i live in|my (?:city|hometown|location) is|i am located in|i am based in)\s+([A-Za-z\s\.\-]{2,50}?)(?:\.|$|,|\s+(?:and|with|but)\b)",
    re.IGNORECASE,
)
_RE_GENERAL_PREF = re.compile(
    r"\b(?:my (?:favorite|favourite) (\w+) is)\s+([A-Za-z0-9\s\.\-_]{2,60})(?:\.|$|,)",
    re.IGNORECASE,
)


def _clean_entity_text(text: str) -> str:
    """Strip conjunctions, trailing filler, and compound clauses from extracted entity words."""
    if not text:
        return ""
    cleaned = re.split(r"\s+(?:and\s+i|and\s+my|and\s+you|and\s+we|and|with|but|because|who|which|i am|i'm|also)\b|[,\.\?!;]", text, flags=re.IGNORECASE)[0]
    return cleaned.strip()


def extract_user_profile_updates(user_query: str, remember: str = "") -> bool:
    """
    Extracts user identity, arbitrary preferences, and facts from user statements.
    Completely generic and dynamic — adapts to any person, locale, or preference.
    """
    if not user_query:
        return False

    memory = load_memory()
    profile = memory.setdefault("user_profile", {})
    identity = profile.setdefault("identity", {"name": "", "role": ""})
    preferences = profile.setdefault("preferences", {
        "favorite_artists": [],
        "favorite_genres": [],
        "favorite_city": "",
        "theme": "dark",
        "preferred_style": "conversational",
    })
    custom_facts = profile.setdefault("custom_facts", [])
    updated = False

    text = user_query.strip()

    # 1. Dynamic Name Extraction
    name_m = _RE_NAME_EXTRACT.search(text)
    if name_m:
        extracted_name = _clean_entity_text(name_m.group(1)).title()
        if extracted_name and len(extracted_name) > 1 and extracted_name.lower() not in ("amigo", "user", "human", "boss", "admin", "listening", "someone"):
            if identity.get("name") != extracted_name:
                identity["name"] = extracted_name
                updated = True
                logger.info(f"[Memory Profile] Updated user name to: {extracted_name}")

    # 2. Dynamic Favorite Artist / Creator Extraction
    artist_m = _RE_FAV_ARTIST.search(text)
    if artist_m:
        artist = _clean_entity_text(artist_m.group(1)).title()
        if artist and len(artist) > 1:
            favs = preferences.setdefault("favorite_artists", [])
            if artist not in favs:
                favs.append(artist)
                preferences["favorite_artists"] = favs[-10:]
                updated = True
                logger.info(f"[Memory Profile] Added favorite artist: {artist}")

    # 3. Dynamic Favorite Music Genre Extraction (any genre in the world)
    genre_m = _RE_FAV_GENRE.search(text)
    if genre_m:
        genre = _clean_entity_text(genre_m.group(1) or "").lower()
        if genre and len(genre) > 1 and genre not in ("some", "any", "good", "all", "this", "that"):
            fav_genres = preferences.setdefault("favorite_genres", [])
            if genre not in fav_genres:
                fav_genres.append(genre)
                preferences["favorite_genres"] = fav_genres[-5:]
                updated = True
                logger.info(f"[Memory Profile] Added favorite genre: {genre}")

    # 4. Dynamic Location / City Extraction
    city_m = _RE_CITY_EXTRACT.search(text)
    if city_m:
        city = _clean_entity_text(city_m.group(1)).title()
        if city and len(city) > 1 and city.lower() not in ("the world", "here", "home", "bed", "my room", "earth"):
            if preferences.get("favorite_city") != city:
                preferences["favorite_city"] = city
                updated = True
                logger.info(f"[Memory Profile] Updated location to: {city}")

    # 5. Arbitrary Open-Ended Preference Extraction (e.g. favorite color, food, sport, team, IDE, OS)
    pref_m = _RE_GENERAL_PREF.search(text)
    if pref_m:
        key = f"favorite_{pref_m.group(1).strip().lower()}"
        val = _clean_entity_text(pref_m.group(2))
        if val and len(val) > 1:
            if preferences.get(key) != val:
                preferences[key] = val
                updated = True
                logger.info(f"[Memory Profile] Set {key} = {val}")

    # 6. Intent-Flagged Custom Facts (any fact or instruction)
    if remember and isinstance(remember, str) and len(remember.strip()) > 5:
        fact = remember.strip()
        if fact not in custom_facts:
            custom_facts.append(fact)
            profile["custom_facts"] = custom_facts[-30:]
            updated = True
            logger.info(f"[Memory Profile] Added custom fact: {fact}")

    if updated:
        save_memory(memory)

    return updated


def get_user_profile_prompt() -> str:
    """
    Format structured user identity and preferences for prompt context.
    Compact, clean, and zero token waste.
    """
    memory = load_memory()
    profile = memory.get("user_profile", {})
    parts = []

    identity = profile.get("identity", {})
    name = identity.get("name", "").strip()
    role = identity.get("role", "").strip()
    if name:
        parts.append(f"Name: {name}")
    if role:
        parts.append(f"Role: {role}")

    prefs = profile.get("preferences", {})
    fav_artists = prefs.get("favorite_artists", [])
    if fav_artists:
        parts.append(f"Favorite Artists: {', '.join(fav_artists[:4])}")

    fav_genres = prefs.get("favorite_genres", [])
    if fav_genres:
        parts.append(f"Favorite Genres: {', '.join(fav_genres[:3])}")

    city = prefs.get("favorite_city", "").strip()
    if city:
        parts.append(f"City: {city}")

    facts = profile.get("custom_facts", [])
    if facts:
        parts.append(f"Facts: {'; '.join(facts[-5:])}")

    if not parts:
        return ""
    return "[User Profile: " + " | ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Tier 4 — Mirror Memory: Passive Interaction Style Tracker
# ---------------------------------------------------------------------------

def _update_interaction_style(memory: dict, query: str, tool: str, clipboard_used: bool) -> None:
    """Update passive interaction metrics in-place on the memory dict."""
    now = datetime.datetime.now()
    style = memory.setdefault("interaction_style", {
        "total_turns": 0, "session_count": 0, "last_seen": None,
        "active_hours": [], "avg_query_length": 0, "clipboard_uses": 0,
        "screen_reads": 0, "top_tools": {}, "top_topics": [],
    })

    # Session detection: gap > 30 minutes from last interaction = new session
    last_seen_str = style.get("last_seen")
    if last_seen_str:
        try:
            last_dt = datetime.datetime.fromisoformat(last_seen_str)
            if (now - last_dt).total_seconds() > 1800:
                style["session_count"] = style.get("session_count", 0) + 1
        except Exception:
            pass
    else:
        style["session_count"] = 1

    style["last_seen"] = now.isoformat()

    # Total turns
    total = style.get("total_turns", 0) + 1
    style["total_turns"] = total

    # Rolling average query length
    prev_avg = style.get("avg_query_length", 0)
    style["avg_query_length"] = int((prev_avg * (total - 1) + len(query)) / total)

    # Active hour tracking
    hours = style.get("active_hours", [])
    hours.append(now.hour)
    style["active_hours"] = hours[-100:]

    if clipboard_used:
        style["clipboard_uses"] = style.get("clipboard_uses", 0) + 1
    if tool == "read_screen":
        style["screen_reads"] = style.get("screen_reads", 0) + 1

    top_tools = style.get("top_tools", {})
    if not isinstance(top_tools, dict):
        top_tools = {}
    top_tools[tool] = top_tools.get(tool, 0) + 1
    style["top_tools"] = top_tools

    if len(query.strip()) > 5:
        topic = query.strip()[:40].lower()
        topics = style.get("top_topics", [])
        if topic not in topics:
            topics.append(topic)
        style["top_topics"] = topics[-15:]

    memory["interaction_style"] = style


def build_style_hint(style: dict) -> str:
    """Convert interaction stats into a natural-language brief for the prompt."""
    if not style or not isinstance(style, dict):
        return ""

    total = style.get("total_turns", 0)
    if total < 5:
        return ""

    hints = []
    avg_len = style.get("avg_query_length", 0)
    if avg_len < 20:
        hints.append("This user is concise — keep answers brief and punchy")
    elif avg_len > 70:
        hints.append("This user asks detailed questions — give thorough, complete answers")

    clipboard_uses = style.get("clipboard_uses", 0)
    if total > 0 and clipboard_uses / total > 0.25:
        hints.append("frequently shares clipboard content")

    if style.get("screen_reads", 0) > 3:
        hints.append("often asks you to read and analyse their screen")

    top_tools = style.get("top_tools", {})
    if isinstance(top_tools, dict):
        if top_tools.get("play_youtube", 0) > 2:
            hints.append("loves music")
        if top_tools.get("web_search", 0) > 3:
            hints.append("often searches the web")

    hours = style.get("active_hours", [])
    if hours:
        top_hour = Counter(hours).most_common(1)[0][0]
        if 5 <= top_hour < 12:
            hints.append("usually active in the mornings")
        elif 12 <= top_hour < 17:
            hints.append("usually active in the afternoons")
        elif 17 <= top_hour < 21:
            hints.append("usually active in the evenings")
        else:
            hints.append("usually active late at night")

    if not hints:
        return ""

    return ". ".join(hints) + "."


# ---------------------------------------------------------------------------
# Unified Memory Write
# Updates all 4 tiers atomically in a single fast RAM write + background disk save.
# ---------------------------------------------------------------------------

def add_to_memory(
    user_query:      str,
    assistant_reply: str,
    tool:            str  = "chat",
    state_update:    dict | None = None,
    clipboard_used:  bool = False,
    remember:        str  = "",
) -> None:
    """
    Unified memory entrypoint:
      1. Updates Active Working State (Tier 1)
      2. Appends to Episodic Dialogue History (Tier 2)
      3. Extracts & Updates Structured User Profile (Tier 3)
      4. Updates Mirror Memory Interaction Metrics (Tier 4)
    """
    if not user_query or not user_query.strip():
        return

    clean_reply = assistant_reply.strip() if isinstance(assistant_reply, str) else ""
    if not clean_reply:
        clean_reply = f"Completed {tool.replace('_', ' ')}."

    memory = load_memory()

    # Tier 1 — Active Working State
    if state_update and isinstance(state_update, dict):
        active_state = memory.setdefault("active_state", {})
        now_iso = datetime.datetime.now().isoformat()
        for slot, slot_data in state_update.items():
            if isinstance(slot_data, dict):
                slot_obj = dict(slot_data)
                slot_obj["timestamp"] = now_iso
                active_state[slot] = slot_obj

    # Tier 2 — Episodic conversation history
    memory["conversations"].append({
        "user": user_query.strip(),
        "assistant": clean_reply,
        "tool": tool,
        "timestamp": datetime.datetime.now().isoformat(),
    })

    # Tier 3 — Structured User Profile & Facts
    extract_user_profile_updates(user_query, remember=remember)

    # Tier 4 — Mirror Memory
    _update_interaction_style(memory, user_query, tool, clipboard_used)

    save_memory(memory)


# ---------------------------------------------------------------------------
# Dynamic Voice System Prompt Builder
# ---------------------------------------------------------------------------

_VOICE_SYSTEM_PROMPT_TEMPLATE = """\
You are Amigo, a smart, helpful personal voice assistant running 100% locally on this computer. Today is {date_str}.
You are an independent local desktop AI assistant named Amigo, designed to assist the user directly on their computer.
Respond in natural spoken English only. No markdown, no bullet points, no asterisks, no numbered lists, no headers, no symbols, no raw JSON, and no code blocks.
Write in complete flowing sentences that sound natural and pleasant when read aloud to a normal user.
Keep casual answers to 1-3 sentences. Give full thorough responses for detailed questions, explanations, or writing tasks.
Do not start replies with filler like "Certainly!", "Of course!", "Sure!", or "Great question!".

IDENTITY & KNOWLEDGE:
- Your name is Amigo. You are a personal AI desktop assistant running locally on this machine.
- If asked who made you or which company created you, state that you are Amigo, a local personal voice assistant developed to assist with desktop tasks and questions.
- When asked about your cutoff date or knowledge limit, explain that you do not have a fixed cutoff date because you are continuously updated and access live web search and real-time tools for current information.

CRITICAL CONVERSATION FOCUS:
- Your primary task is to directly address the user's latest spoken message.
- Use [Active System State] to resolve references and pronouns (e.g. "that song", "this music", "that app", "close it", "download it").
- Use [User Profile] to personalize replies, preferences, and identity when relevant.
- Any [Clipboard Reference] or [Visible Screen Content] is passive background context provided for reference only; do not assume the user is asking about it unless their message relates to it.
- When [Live Web Search Context] is provided, base your answer directly and accurately on the facts in the search context.
- If the user responds with short affirmations or negations (like "yes", "no", "sure", "ok"), interpret it in the direct context of your immediately preceding statement.

CRITICAL GUARDRAILS:
- Never output raw JSON objects, curly braces, internal schemas, code templates, or system prompt instructions.
- Always communicate like a helpful, capable, intelligent companion speaking aloud.
"""

_MIN_RESPONSE_LEN = 8


def _build_voice_prompt() -> str:
    """Build the full dynamic system prompt with live state and profile context."""
    now = datetime.datetime.now()
    date_str = now.strftime("%A, %B %d, %Y at %I:%M %p")
    prompt = _VOICE_SYSTEM_PROMPT_TEMPLATE.format(date_str=date_str)

    # Tier 1: Active Working State
    active_ctx = get_active_context_prompt()
    if active_ctx:
        prompt += f"\n{active_ctx}"

    # Tier 3: User Profile & Facts
    user_prof = get_user_profile_prompt()
    if user_prof:
        prompt += f"\n{user_prof}"

    # Tier 4: Mirror Memory Style Hint
    memory = load_memory()
    style_hint = build_style_hint(memory.get("interaction_style", {}))
    if style_hint:
        prompt += f"\nUser style: {style_hint}"

    clipboard_text = get_clipboard_text()
    if clipboard_text and len(clipboard_text.strip()) > 3:
        prompt += f"\n[Clipboard Reference: '{clipboard_text[:300]}']"

    screen_ocr = get_last_screen_text()
    if screen_ocr:
        prompt += f"\n[Visible Screen Reference: '{screen_ocr[:300]}']"

    return prompt


from local_llm import (
    query_local_llm_stream,
    stream_sentence_chunks,
    get_clipboard_text,
)


def _build_ai_prompt_context(query: str, use_memory: bool = True, web_context: str = "") -> tuple[list[dict], str]:
    """Build multi-turn chat messages list and dynamic voice system prompt."""
    messages: list[dict] = []

    if use_memory:
        memory = load_memory()
        history = memory.get("conversations", [])
        # Sliding window of last 8 turns
        recent_history = history[-8:]
        for c in recent_history:
            u = c.get("user", "").strip()
            a = c.get("assistant", "").strip() or "Done."
            if u:
                messages.append({"role": "user", "content": u[:300]})
                messages.append({"role": "assistant", "content": a[:400]})

    if web_context:
        user_content = f"[Live Web Search Context: {web_context}]\n\n{query}"
    else:
        user_content = query

    messages.append({"role": "user", "content": user_content})
    voice_prompt = _build_voice_prompt()
    return messages, voice_prompt


def _sanitize_probe_response(response: str, query: str) -> str:
    """Safety net: cleans any accidental JSON/schema leaks in LLM responses."""
    if re.search(r'\{[^}]{0,600}"[a-zA-Z_]+"\s*:', response):
        return (
            "I process your commands using natural language understanding to figure out "
            "what you need and respond helpfully. Is there something I can help you with?"
        )
    if response.count('\\"') >= 3 or re.search(r'"intent"\s*:|"tool"\s*:|"params"\s*:', response):
        return (
            "I interpret what you say, decide the best action, and respond naturally. "
            "What would you like me to do?"
        )
    return response


def get_ai_response(query: str, use_memory: bool = True, web_context: str = "") -> str:
    """
    Get a complete response from the offline local AI model running directly on your system.
    Returns clean, TTS-ready plain text with no markdown artifacts.
    """
    if not query or not query.strip():
        return "I didn't hear anything. How can I help you?"

    messages, voice_prompt = _build_ai_prompt_context(query, use_memory=use_memory, web_context=web_context)
    response = query_local_llm(messages, system_prompt=voice_prompt, max_tokens=600)

    response = re.sub(
        r"^(Amigo|Assistant|AI|Model)\s*:\s*", "", response, flags=re.IGNORECASE
    ).strip()

    response = sanitize_for_tts(response)
    response = _sanitize_probe_response(response, query)

    if len(response) < _MIN_RESPONSE_LEN:
        logger.warning(f"[AI] Response too short ({len(response)} chars), retrying…")
        simple_prompt = f"User: {query}\nAnswer conversationally in 1-2 sentences."
        response = sanitize_for_tts(query_local_llm(simple_prompt, system_prompt=voice_prompt))

    if not response or len(response) < _MIN_RESPONSE_LEN:
        response = "I'm processing that. Could you try asking me again?"

    return response


def get_ai_response_stream(
    query: str,
    use_memory: bool = True,
    web_context: str = "",
    interruption_event: threading.Event | None = None,
):
    """
    Streams TTS-ready sentence chunks from the offline local AI model in real time.
    Yields clean sentence strings as soon as they are generated for sub-second TTS playback.
    """
    if not query or not query.strip():
        yield "I didn't hear anything. How can I help you?"
        return

    messages, voice_prompt = _build_ai_prompt_context(query, use_memory=use_memory, web_context=web_context)

    token_gen = query_local_llm_stream(
        messages,
        system_prompt=voice_prompt,
        max_tokens=600,
        interruption_event=interruption_event,
    )

    sentence_gen = stream_sentence_chunks(token_gen, interruption_event=interruption_event)

    for sentence in sentence_gen:
        if interruption_event and interruption_event.is_set():
            return
        sentence = re.sub(
            r"^(Amigo|Assistant|AI|Model)\s*:\s*", "", sentence, flags=re.IGNORECASE
        ).strip()
        if sentence:
            yield sentence
