"""
Content Generation, Regeneration & Dynamic Routing Service for Amigo Voice Assistant.
Handles date/calendar resolution, user profile context from RAG, stateful revision tracking,
and multi-destination output routing without hardcoded fallback strings or rigid destination lists.
"""

import datetime
import logging
import re
import time
import urllib.parse
import webbrowser
from typing import Any, Dict, Optional, Tuple

import amigo.core.rag_engine as rag_engine
import amigo.services.os_automation as os_automation
from amigo.services.app_opener import open_windows_app

logger = logging.getLogger("amigo.content_generator")


def _get_ai_response(*args, **kwargs) -> str:
    """Forwarder to llm_agent to avoid circular imports."""
    from amigo.core.llm_agent import get_ai_response
    return get_ai_response(*args, **kwargs)


def _is_app_running(app_name: str) -> bool:
    """Dynamically checks if an application process is already open and running."""
    target = (app_name or "").lower().strip()
    if not target or target in ("app", "window", "folder", "file"):
        return False
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower().replace(".exe", "")
            if target == name or bool(re.search(rf"\b{re.escape(target)}\b", name)):
                return True
    except Exception:
        pass
    return False


def build_temporal_and_user_context() -> str:
    """
    Dynamically builds real-time date/calendar context and user identity from RAG profile.
    No hardcoded dates, names, or values.
    """
    now = datetime.datetime.now()
    tomorrow = now + datetime.timedelta(days=1)
    day_after = now + datetime.timedelta(days=2)
    next_week = now + datetime.timedelta(days=7)

    upcoming_days = []
    for i in range(1, 8):
        future_date = now + datetime.timedelta(days=i)
        upcoming_days.append(f"{future_date.strftime('%A')}: {future_date.strftime('%B %d, %Y')}")

    temporal_ctx = (
        f"Current Date & Time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}\n"
        f"Tomorrow: {tomorrow.strftime('%A, %B %d, %Y')}\n"
        f"Day After Tomorrow: {day_after.strftime('%A, %B %d, %Y')}\n"
        f"One Week From Today: {next_week.strftime('%A, %B %d, %Y')}\n"
        f"Upcoming 7-Day Reference: {' | '.join(upcoming_days)}"
    )

    user_ctx = ""
    try:
        profile = rag_engine.load_profile()
        if isinstance(profile, dict) and profile:
            name = profile.get("name") or profile.get("user_name") or profile.get("preferences", {}).get("name", "")
            role = profile.get("role") or profile.get("preferences", {}).get("role", "")
            details = [f"{k}: {v}" for k, v in profile.get("preferences", {}).items() if v]
            if name:
                user_ctx += f"User Name: {name}\n"
            if role:
                user_ctx += f"User Role: {role}\n"
            if details:
                user_ctx += "User Preferences: " + ", ".join(details[:5])
    except Exception:
        pass

    return f"{temporal_ctx}\n{user_ctx}".strip()


def route_content_to_destination(
    content: str,
    destination: str,
    content_type: str = "text",
    subject: str = "",
    recipient: str = "",
) -> Tuple[bool, str]:
    """
    Dynamically routes generated content to ANY user-specified destination without hardcoding fixed lists.
    Supports clipboard, webmail (Gmail, etc.), mail clients (Outlook), or any desktop editor/IDE.
    """
    dest_clean = (destination or "").strip()
    dest_low = dest_clean.lower()

    if not dest_clean or dest_low in ("none", "panel", "screen", "ui", "chat", "ask"):
        return False, ""

    import pyperclip

    # Always copy to clipboard first as instant backup
    try:
        pyperclip.copy(content)
    except Exception as e:
        logger.warning(f"[Content Router] Clipboard copy warning: {e}")

    # 1. Pure Clipboard Destination
    if any(k in dest_low for k in ("clipboard", "copy")):
        return True, "Copied the content to your clipboard."

    # 2. Gmail / Webmail Compose via URL Scheme
    if "gmail" in dest_low or "google mail" in dest_low:
        lines = content.strip().splitlines()
        extracted_subj = subject
        body_text = content.strip()
        if lines and lines[0].lower().startswith("subject:"):
            extracted_subj = lines[0].split(":", 1)[1].strip()
            body_text = "\n".join(lines[1:]).strip()

        gmail_url = (
            f"https://mail.google.com/mail/?view=cm&fs=1"
            f"&to={urllib.parse.quote(recipient)}"
            f"&su={urllib.parse.quote(extracted_subj)}"
            f"&body={urllib.parse.quote(body_text)}"
        )
        webbrowser.open(gmail_url)
        return True, "Opened Gmail with your composed draft."

    # 3. Outlook / System Mail Integration
    if "outlook" in dest_low:
        try:
            from amigo.services.mail_integration import draft_email, is_outlook_available
            if is_outlook_available():
                lines = content.strip().splitlines()
                extracted_subj = subject
                body_text = content.strip()
                if lines and lines[0].lower().startswith("subject:"):
                    extracted_subj = lines[0].split(":", 1)[1].strip()
                    body_text = "\n".join(lines[1:]).strip()
                draft_email(to=recipient, subject=extracted_subj, body=body_text)
                return True, "Created the draft in Microsoft Outlook."
        except Exception as e:
            logger.warning(f"[Content Router] Outlook draft fallback: {e}")

    # 4. Current Active Window
    if dest_low in ("here", "current window", "active window", "current app", "active app"):
        time.sleep(0.3)
        os_automation.type_text(content)
        return True, "Inserted the content into your active window."

    # 5. Any Desktop Application (Notepad, Word, VS Code, Slack, Discord, etc.)
    opened = open_windows_app(dest_clean)
    if opened or _is_app_running(dest_clean):
        time.sleep(1.5)  # Allow application window to focus
        os_automation.type_text(content)
        return True, f"Opened {dest_clean.title()} and inserted your {content_type}."

    # Fallback if the requested app couldn't be launched directly
    return True, f"I couldn't launch {dest_clean.title()} directly, so I copied the {content_type} to your clipboard ready to paste."


def generate_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """
    Dynamically generates any content (email, code, poem, letter, essay, script, etc.)
    with full date awareness, tone control, and optional destination routing.
    """
    params = params if isinstance(params, dict) else {}
    content_type = str(params.get("type") or "content").strip()
    topic = str(params.get("topic") or query).strip()
    tone = str(params.get("tone") or "appropriate to the request").strip()
    destination = str(params.get("destination") or "").strip()
    recipient = str(params.get("recipient") or "").strip()
    extra_context = str(params.get("context") or "").strip()

    sys_context = build_temporal_and_user_context()

    generation_prompt = (
        f"You are an expert writer and coder. Generate a {content_type} based on the user's request.\n\n"
        f"--- REAL-TIME SYSTEM & DATE CONTEXT ---\n{sys_context}\n"
        f"--- REQUEST DETAILS ---\n"
        f"User Query: {query}\n"
        f"Topic / Purpose: {topic}\n"
        f"Tone / Style: {tone}\n"
        f"{f'Recipient: {recipient}' if recipient else ''}\n"
        f"{f'Additional Context: {extra_context}' if extra_context else ''}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Resolve all relative dates ('tomorrow', 'next Monday', 'for 3 days starting tomorrow') into exact, explicit calendar dates (including day of the week and full date) using the Real-Time System & Date Context above.\n"
        f"2. If user name or details are present in the context, use them naturally; if not, use clean bracketed placeholders like [Your Name] instead of inventing fake names.\n"
        f"3. Output ONLY the raw {content_type} itself (e.g., include 'Subject: ...' if it's an email, or raw code if code is requested). Do NOT wrap in conversational filler or explanations."
    )

    generated_content = _get_ai_response(
        generation_prompt,
        use_memory=True,
        is_voice=False,
        max_tokens=800,
    ).strip()

    state_payload = {
        "content": generated_content,
        "type": content_type,
        "topic": topic,
        "tone": tone,
        "destination": destination,
        "recipient": recipient,
        "history": [generated_content],
        "timestamp": time.time(),
    }
    try:
        rag_engine.update_active_state("generated_content", state_payload)
    except Exception as e:
        logger.debug(f"[Generate Content] State update error: {e}")

    # Route to destination if user specified one; otherwise ask where they want it
    routed, route_msg = route_content_to_destination(
        generated_content,
        destination,
        content_type=content_type,
        subject=topic if len(topic) < 60 else f"{content_type.title()}",
        recipient=recipient,
    )

    if routed:
        spoken_reply = f"I've written the {content_type} for {topic}. {route_msg} Let me know if you'd like to regenerate it or change the tone."
    else:
        spoken_reply = (
            f"I've drafted your {content_type} for {topic}. "
            f"Where would you like me to insert it—such as Gmail, Notepad, Word, or your clipboard? "
            f"Or tell me if you'd like me to regenerate it with a different style."
        )

    return spoken_reply, None, {
        "generated_content": generated_content,
        "content_type": content_type,
        "topic": topic,
        "tone": tone,
        "destination": destination or "pending",
        "routed": routed,
        "showGeneratedPanel": True,
    }


def regenerate_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """
    Refines, modifies, or regenerates the previously generated content based on user feedback
    (e.g., 'make it more friendly', 'make it shorter', 'convert it to Python', 'add a line about...').
    """
    params = params if isinstance(params, dict) else {}
    feedback = str(params.get("feedback") or params.get("instructions") or params.get("tone") or query).strip()
    new_destination = str(params.get("destination") or "").strip()

    state = rag_engine.get_active_state(clean_expired=False) or {}
    gen_state = state.get("generated_content") if isinstance(state, dict) else None

    if not isinstance(gen_state, dict) or not gen_state.get("content"):
        # If no prior draft exists in state, fall back to fresh generation
        return generate_content(params, query, spoken)

    previous_content = gen_state["content"]
    content_type = str(params.get("type") or gen_state.get("type") or "content").strip()
    topic = gen_state.get("topic", "")
    recipient = gen_state.get("recipient", "")
    target_dest = new_destination or gen_state.get("destination", "")

    sys_context = build_temporal_and_user_context()

    regen_prompt = (
        f"You are refining and regenerating an existing {content_type} based on the user's feedback.\n\n"
        f"--- REAL-TIME SYSTEM & DATE CONTEXT ---\n{sys_context}\n\n"
        f"--- PREVIOUS DRAFT ---\n{previous_content}\n\n"
        f"--- USER FEEDBACK / MODIFICATION REQUEST ---\n{feedback} (Full query: {query})\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Rewrite the {content_type} applying the requested tone, style, length, or content changes.\n"
        f"2. Keep all resolved calendar dates accurate unless the user asked to change the dates.\n"
        f"3. Output ONLY the updated {content_type} without any meta-commentary or intro text."
    )

    updated_content = _get_ai_response(
        regen_prompt,
        use_memory=True,
        is_voice=False,
        max_tokens=800,
    ).strip()

    history = gen_state.get("history", [])
    if isinstance(history, list):
        history.append(updated_content)

    updated_state = {
        **gen_state,
        "content": updated_content,
        "type": content_type,
        "tone": feedback,
        "destination": target_dest,
        "history": history[-5:],
        "timestamp": time.time(),
    }
    try:
        rag_engine.update_active_state("generated_content", updated_state)
    except Exception:
        pass

    routed, route_msg = False, ""
    if new_destination:
        routed, route_msg = route_content_to_destination(
            updated_content,
            new_destination,
            content_type=content_type,
            subject=topic,
            recipient=recipient,
        )
    else:
        try:
            import pyperclip
            pyperclip.copy(updated_content)
        except Exception:
            pass

    if routed:
        spoken_reply = f"I've regenerated the {content_type} to be {feedback}. {route_msg}"
    else:
        spoken_reply = (
            f"I've updated the {content_type} based on your feedback. "
            f"Would you like me to insert this version into Gmail, Notepad, Word, or your clipboard, or tweak it further?"
        )

    return spoken_reply, None, {
        "generated_content": updated_content,
        "content_type": content_type,
        "topic": topic,
        "tone": feedback,
        "destination": target_dest or "pending",
        "routed": routed,
        "is_regenerated": True,
        "showGeneratedPanel": True,
    }


def insert_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """
    Inserts the active generated content (or custom provided text) into the user's chosen destination
    (e.g., Gmail, Notepad, Word, VS Code, Clipboard, or active window).
    """
    params = params if isinstance(params, dict) else {}
    content = str(params.get("content") or "").strip()
    destination = str(params.get("destination") or params.get("app") or params.get("target") or "").strip()

    state = rag_engine.get_active_state(clean_expired=False) or {}
    gen_state = state.get("generated_content") if isinstance(state, dict) else {}
    if not isinstance(gen_state, dict):
        gen_state = {}

    if not content:
        content = gen_state.get("content", "")

    if not content:
        return "There isn't any generated content waiting to be inserted right now. Ask me to write something first!", None, None

    content_type = gen_state.get("type", "content")
    topic = gen_state.get("topic", "")
    recipient = gen_state.get("recipient", "")

    if not destination:
        destination = "active window"

    routed, route_msg = route_content_to_destination(
        content,
        destination,
        content_type=content_type,
        subject=topic,
        recipient=recipient,
    )

    if routed:
        try:
            rag_engine.update_active_state("generated_content", {**gen_state, "destination": destination})
        except Exception:
            pass
        return route_msg, None, {
            "generated_content": content,
            "destination": destination,
            "status": "inserted",
        }

    return "Where would you like me to insert the content? You can say Gmail, Notepad, Word, Clipboard, or the current window.", None, None


def type_text(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str]]:
    """
    Types literal text into an app, or dynamically delegates to generate_content
    if the user is asking to compose/draft something.
    No hardcoded phrases or fallback names.
    """
    params = params if isinstance(params, dict) else {}
    text = str(params.get("text", "")).strip()
    app = str(params.get("app", "")).strip()

    # Dynamically detect if 'text' is actually a creative/drafting prompt rather than literal dictation
    is_draft_request = bool(re.search(
        r"^(?:an?\s+)?(?:email|e-mail|letter|message|note|essay|application|poem|code|script|function|story|post)\b",
        text,
        re.IGNORECASE,
    ))
    if is_draft_request:
        result = generate_content(
            {"type": text.split()[0] if text else "content", "topic": query or text, "destination": app},
            query,
            spoken,
        )
        return result[0], result[1]

    if app:
        open_windows_app(app)
        time.sleep(1.2)

    if text:
        os_automation.type_text(text)

    app_desc = f" into {app}" if app else ""
    return (f"Typed content{app_desc}." if text else "Typed text."), None
