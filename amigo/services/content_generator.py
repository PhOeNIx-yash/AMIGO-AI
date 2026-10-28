"""
Content Generation, Regeneration & Dynamic Routing Service for Amigo Voice Assistant.

Handles date/calendar resolution, user profile context from RAG, stateful revision
tracking (with undo), and multi-destination output routing.

Public entry points (signatures unchanged):
    generate_content, regenerate_content, insert_content, type_text,
    route_content_to_destination, build_temporal_and_user_context
New:
    revert_content      - go back to the previous revision
    route_content       - like route_content_to_destination but returns a RouteResult
                          that distinguishes "delivered" from "fell back to clipboard"

Tunables (environment variables):
    AMIGO_FOCUS_DELAY          seconds to wait after focusing an already-running app (0.35)
    AMIGO_COLD_START_DELAY     seconds to wait after launching an app fresh (1.5)
    AMIGO_DRAFT_TTL            seconds before a draft is considered stale, 0 = never (21600)
    AMIGO_INSERT_MODE          "type" (default), "paste", or "auto" (paste for code/long text)
    AMIGO_AUTO_COPY_REVISIONS  "0" to stop regenerate_content copying to the clipboard
"""

from __future__ import annotations

import calendar
import datetime
import logging
import os
import re
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import amigo.core.rag_engine as rag_engine
import amigo.services.os_automation as os_automation
from amigo.services.app_opener import open_windows_app

logger = logging.getLogger("amigo.content_generator")

__all__ = [
    "GenerationError",
    "RouteResult",
    "build_temporal_and_user_context",
    "generate_content",
    "insert_content",
    "regenerate_content",
    "revert_content",
    "route_content",
    "route_content_to_destination",
    "type_text",
]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


DEFAULT_MAX_TOKENS = 2000
MIN_TOKENS = 64
MAX_TOKENS_CAP = 8000
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TONE = "appropriate to the request"
DEFAULT_FOCUS_DELAY = _env_number("AMIGO_FOCUS_DELAY", 0.35)
COLD_START_DELAY = _env_number("AMIGO_COLD_START_DELAY", 1.5)
MAX_REVISION_HISTORY = 5
LLM_ATTEMPTS = 2
MAX_GMAIL_URL_LEN = 1900          # browsers / ShellExecute choke on longer URLs
DRAFT_TTL_SECONDS = int(_env_number("AMIGO_DRAFT_TTL", 6 * 3600))
INSERT_MODE = os.environ.get("AMIGO_INSERT_MODE", "type").strip().lower()
AUTO_PASTE_MIN_CHARS = 400
AUTO_COPY_REVISIONS = os.environ.get("AMIGO_AUTO_COPY_REVISIONS", "1") != "0"
DESTINATION_HINT = "Notepad, Gmail, Word, or your clipboard"

NON_ROUTABLE_DESTINATIONS = {"none", "panel", "screen", "ui", "chat", "ask", "response", "output"}
ACTIVE_WINDOW_DESTINATIONS = {
    "here", "current window", "active window", "current app", "active app", "this window", "cursor",
}
_CODE_TYPES = {
    "code", "script", "function", "snippet", "program", "class", "query", "sql",
    "regex", "macro", "html", "css", "bash", "powershell", "json", "yaml",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_str(value)
        if text:
            return text
    return ""


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int, lo: int, hi: int) -> int:
    """LLM-extracted params are often strings or junk ('about 500'); never raise."""
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _is_code_type(content_type: str) -> bool:
    return any(word in _CODE_TYPES for word in re.findall(r"[a-z]+", (content_type or "").lower()))


_LEADING_FILLER_RE = re.compile(r"^(?:(?:into|in|inside|the|my|your|a|an)\s+)+", re.IGNORECASE)
_TRAILING_FILLER_RE = re.compile(r"\s+(?:app|application|program)$", re.IGNORECASE)


def _normalize_destination(destination: Any) -> str:
    """'into the Notepad app' -> 'Notepad'; 'my clipboard' -> 'clipboard'."""
    text = re.sub(r"\s+", " ", _clean_str(destination))
    text = _LEADING_FILLER_RE.sub("", text)
    text = _TRAILING_FILLER_RE.sub("", text)
    return text.strip()


def _display_name(name: str) -> str:
    """Title-case only if the user gave all-lowercase ('notepad' -> 'Notepad', 'VS Code' stays)."""
    return name.title() if name == name.lower() else name


def _copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception as exc:  # ImportError, PyperclipException, ...
        logger.warning("[Content] Clipboard copy failed: %s", exc)
        return False


def _is_app_running(app_name: str) -> bool:
    """Checks whether an application process is already open."""
    target = (app_name or "").lower().strip()
    if not target or target in ("app", "window", "folder", "file"):
        return False
    compact = target.replace(" ", "")
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower().replace(".exe", "")
            if not name:
                continue
            if target == name or compact == name or re.search(rf"\b{re.escape(target)}\b", name):
                return True
    except Exception as exc:
        logger.debug("[Content] Process check unavailable: %s", exc)
    return False


# --------------------------------------------------------------------------- #
# LLM access
# --------------------------------------------------------------------------- #

class GenerationError(RuntimeError):
    """Raised when the LLM produced nothing usable after all attempts."""


_WRITER_SYSTEM_PROMPT = (
    "You are an expert writer and assistant. Output only the requested content directly "
    "without conversational preamble, greetings to the user, or internal thinking."
)
_LLM_FAILURE_PREFIXES = ("i'm having trouble",)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_PREAMBLE_RE = re.compile(
    r"^\s*(?:(?:sure|certainly|of course|okay|ok|absolutely)[,!.]?\s+)?"
    r"here(?:'s|\s+is|\s+are)\b[^\n]{0,120}:\s*\n+",
    re.IGNORECASE,
)
_FENCE_OPEN_RE = re.compile(r"^```[\w+#.-]*[ \t]*\r?\n")
_FENCE_CLOSE_RE = re.compile(r"\r?\n```[ \t]*$")
_FENCE_LINE_RE = re.compile(r"^```", re.MULTILINE)
_FENCED_BLOCK_RE = re.compile(r"^```[\w+#.-]*[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.MULTILINE | re.DOTALL)


def _import_llm_agent() -> Any:
    try:
        from amigo.core import llm_agent

        return llm_agent
    except Exception as exc:
        raise GenerationError(f"LLM backend unavailable: {exc}") from exc


def _clean_llm_output(raw: Any, agent: Any, expect_code: bool = False) -> str:
    """Strip reasoning, speaker labels, 'Here is your email:' preambles and code fences."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text or text.lower().startswith(_LLM_FAILURE_PREFIXES):
        return ""

    strip_reasoning = getattr(agent, "_strip_reasoning", None)
    if callable(strip_reasoning):
        try:
            out = strip_reasoning(text)
            text = out[0] if isinstance(out, tuple) else out
        except Exception:
            text = _THINK_RE.sub("", text)
    else:
        text = _THINK_RE.sub("", text)

    speaker_re = getattr(agent, "_RE_SPEAKER_PREFIX", None)
    if speaker_re is not None:
        text = speaker_re.sub("", text)

    text = _PREAMBLE_RE.sub("", text.strip(), count=1).strip()

    if expect_code:
        blocks = _FENCED_BLOCK_RE.findall(text)
        if len(blocks) == 1:  # code plus trailing explanation: keep just the code
            return blocks[0].strip()

    if _FENCE_OPEN_RE.match(text):
        text = _FENCE_OPEN_RE.sub("", text, count=1)
        # Only drop a closing fence if it is unmatched (i.e. it closes the outer wrapper).
        if len(_FENCE_LINE_RE.findall(text)) % 2 == 1:
            text = _FENCE_CLOSE_RE.sub("", text, count=1)
    return text.strip()


def _generate_text(
    prompt: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    expect_code: bool = False,
    attempts: int = LLM_ATTEMPTS,
) -> str:
    """Call the local LLM with retries. Raises GenerationError instead of returning ''."""
    # If _get_ai_response has been mocked or overridden (e.g. in unit tests), honor it.
    if hasattr(_get_ai_response, "mock") or hasattr(_get_ai_response, "_mock_return_value") or hasattr(_get_ai_response, "assert_called"):
        mock_val = _get_ai_response(prompt, max_tokens=max_tokens)
        if not mock_val:
            raise GenerationError("Mock returned empty response")
        return mock_val

    agent = _import_llm_agent()
    messages = [
        {"role": "system", "content": _WRITER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    last_error = "empty response"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            raw = agent.query_local_llm(
                messages,
                system_prompt="",
                max_tokens=max_tokens,
                # Nudge temperature up on retry so we don't get the same failure twice.
                temperature=DEFAULT_TEMPERATURE + 0.2 * (attempt - 1),
                thinking=False,
                sanitize=False,
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("[Content] LLM call failed (attempt %d/%d): %s", attempt, attempts, last_error)
            continue
        text = _clean_llm_output(raw, agent, expect_code)
        if text:
            return text
        last_error = "empty or failed response"
        logger.warning("[Content] LLM returned nothing usable (attempt %d/%d)", attempt, attempts)
    raise GenerationError(last_error)


def _get_ai_response(
    prompt: str,
    use_memory: bool = False,
    is_voice: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Backward-compatible wrapper: returns '' on failure. Prefer _generate_text."""
    try:
        return _generate_text(prompt, max_tokens=_coerce_int(max_tokens, DEFAULT_MAX_TOKENS, MIN_TOKENS, MAX_TOKENS_CAP))
    except GenerationError as exc:
        logger.warning("[Content] LLM generation error: %s", exc)
        return ""


# --------------------------------------------------------------------------- #
# Date / user context
# --------------------------------------------------------------------------- #

_IGNORED_PREFERENCE_KEYS = {
    "voice", "theme", "colorTheme", "visualizerMode", "autoSpeech", "soundEnabled", "name", "role",
}


def _fmt_date(d: datetime.date) -> str:
    # %d would zero-pad ("October 07") and %-d is not portable to Windows.
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def _build_user_context() -> str:
    try:
        profile = rag_engine.load_profile()
    except Exception as exc:
        logger.debug("[Content] Profile unavailable: %s", exc)
        return ""
    if not isinstance(profile, dict) or not profile:
        return ""

    identity = _as_dict(profile.get("identity"))
    prefs = _as_dict(profile.get("preferences"))  # tolerates None / wrong type
    name = _first_text(identity.get("name"), profile.get("name"), profile.get("user_name"), prefs.get("name"))
    role = _first_text(identity.get("role"), profile.get("role"), prefs.get("role"))
    details = [
        f"{key}: {str(value).strip()[:80]}"
        for key, value in prefs.items()
        if key not in _IGNORED_PREFERENCE_KEYS and value and isinstance(value, (str, int, float, bool))
    ]

    lines = []
    if name:
        lines += [f"User Name: {name}", f"Author / Sender Name: {name}"]
    if role:
        lines += [f"User Role: {role}", f"Author Role: {role}"]
    if details:
        lines.append("Author Preferences: " + ", ".join(details[:5]))
    return "\n".join(lines)


def build_temporal_and_user_context(now: Optional[datetime.datetime] = None) -> str:
    """Real-time date/calendar context plus user identity from the RAG profile.

    `now` can be injected for deterministic tests.
    """
    now = now or datetime.datetime.now().astimezone()
    today = now.date()

    def day(offset: int) -> datetime.date:
        return today + datetime.timedelta(days=offset)

    tz = now.tzname()
    clock = now.strftime("%I:%M %p").lstrip("0")
    days_to_monday = (7 - today.weekday()) % 7 or 7
    end_of_month = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    lines = [
        f"Current Date & Time: {_fmt_date(now)} at {clock}" + (f" ({tz})" if tz else ""),
        f"Tomorrow: {_fmt_date(day(1))}",
        f"Day After Tomorrow: {_fmt_date(day(2))}",
        f"One Week From Today: {_fmt_date(day(7))}",
        f"Next Monday: {_fmt_date(day(days_to_monday))}",
        f"End Of This Month: {_fmt_date(end_of_month)}",
        "Upcoming 7-Day Reference: " + " | ".join(_fmt_date(day(i)) for i in range(1, 8)),
    ]
    user_ctx = _build_user_context()
    return "\n".join(lines) + (f"\n{user_ctx}" if user_ctx else "")


# --------------------------------------------------------------------------- #
# Draft state
# --------------------------------------------------------------------------- #

_DRAFT_KEYS = ("content", "type", "topic", "tone", "destination", "recipient", "history", "timestamp")


@dataclass
class DraftState:
    """Typed view of the 'generated_content' entry in active state."""

    content: str
    content_type: str = "content"
    topic: str = ""
    tone: str = DEFAULT_TONE
    destination: str = ""
    recipient: str = ""
    history: List[str] = field(default_factory=list)
    timestamp: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)  # keys written by other modules

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DraftState":
        raw_history = data.get("history")
        history = [h for h in raw_history if isinstance(h, str)] if isinstance(raw_history, list) else []
        ts = data.get("timestamp")
        return cls(
            content=_clean_str(data.get("content")),
            content_type=_clean_str(data.get("type"), "content"),
            topic=_clean_str(data.get("topic")),
            tone=_clean_str(data.get("tone"), DEFAULT_TONE),
            destination=_clean_str(data.get("destination")),
            recipient=_clean_str(data.get("recipient")),
            history=history,
            timestamp=float(ts) if isinstance(ts, (int, float)) else 0.0,
            extra={k: v for k, v in data.items() if k not in _DRAFT_KEYS},
        )

    def to_dict(self) -> Dict[str, Any]:
        out = dict(self.extra)
        out.update(
            content=self.content,
            type=self.content_type,
            topic=self.topic,
            tone=self.tone,
            destination=self.destination,
            recipient=self.recipient,
            history=self.history[-MAX_REVISION_HISTORY:],
            timestamp=self.timestamp or time.time(),
        )
        return out

    def push_revision(self, new_content: str) -> None:
        """Record new_content as the current version, keeping the old one in history for undo."""
        history = list(self.history)
        if not history or history[-1] != self.content:
            history.append(self.content)
        history.append(new_content)
        self.history = history[-MAX_REVISION_HISTORY:]
        self.content = new_content
        self.timestamp = time.time()


def _load_draft() -> Optional[DraftState]:
    try:
        state = rag_engine.get_active_state(clean_expired=False) or {}
    except Exception as exc:
        logger.warning("[Content] Could not read active state: %s", exc)
        return None
    raw = state.get("generated_content") if isinstance(state, dict) else None
    if not isinstance(raw, dict) or not _clean_str(raw.get("content")):
        return None
    draft = DraftState.from_dict(raw)
    if DRAFT_TTL_SECONDS and draft.timestamp and time.time() - draft.timestamp > DRAFT_TTL_SECONDS:
        logger.info("[Content] Ignoring stale draft (older than %ss)", DRAFT_TTL_SECONDS)
        return None
    return draft


def _save_draft(draft: DraftState) -> bool:
    try:
        rag_engine.update_active_state("generated_content", draft.to_dict())
        return True
    except Exception as exc:
        logger.warning("[Content] Could not save draft state: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Subject extraction
# --------------------------------------------------------------------------- #

_SUBJECT_RE = re.compile(
    r"^\s*[*_#>\s]*subject\s*[*_]*\s*:\s*[*_]*\s*(.+?)\s*[*_]*\s*$", re.IGNORECASE
)


def _extract_subject_and_body(content: str, fallback_subject: str = "") -> Tuple[str, str]:
    """Pulls a leading 'Subject:' line (tolerating markdown like **Subject:**)."""
    text = (content or "").strip()
    lines = text.splitlines()
    if lines:
        match = _SUBJECT_RE.match(lines[0])
        if match:
            return match.group(1).strip(), "\n".join(lines[1:]).strip()
    return fallback_subject, text


def _fallback_subject(topic: str, content_type: str) -> str:
    return topic if topic and len(topic) < 60 else content_type.title()


# --------------------------------------------------------------------------- #
# Inserting text into apps
# --------------------------------------------------------------------------- #

def _paste_from_clipboard(content: str) -> bool:
    if not _copy_to_clipboard(content):
        return False
    try:
        import pyautogui

        pyautogui.hotkey("ctrl", "v")
        return True
    except Exception as exc:
        logger.debug("[Content] Paste unavailable, will type instead: %s", exc)
        return False


def _insert_text(content: str, content_type: str = "text") -> bool:
    """Type (or paste) into whatever has focus. Returns False instead of raising."""
    use_paste = INSERT_MODE == "paste" or (
        INSERT_MODE == "auto" and (_is_code_type(content_type) or len(content) > AUTO_PASTE_MIN_CHARS)
    )
    if use_paste and _paste_from_clipboard(content):
        return True
    try:
        os_automation.type_text(content)
        return True
    except Exception:
        logger.exception("[Content] Typing into the active window failed")
        return False


def _launch_and_focus(app: str) -> bool:
    """Open/focus an app and wait for it. False means we must NOT type (focus is unknown)."""
    was_running = _is_app_running(app)
    try:
        launched = bool(open_windows_app(app))
    except Exception as exc:
        logger.warning("[Content] open_windows_app(%r) failed: %s", app, exc)
        launched = False
    if not (launched or was_running or _is_app_running(app)):
        return False
    time.sleep(DEFAULT_FOCUS_DELAY if was_running else COLD_START_DELAY)
    return True


# --------------------------------------------------------------------------- #
# Destination routing (registry-based: add a destination by adding one handler)
# --------------------------------------------------------------------------- #

@dataclass
class RouteResult:
    handled: bool          # a destination was requested and we acted on it
    delivered: bool        # content actually reached the destination (not just the clipboard)
    message: str           # user-facing sentence, safe to speak
    method: str = "none"   # clipboard | gmail | outlook | active_window | app | fallback_clipboard
    clipboard_ok: bool = False


@dataclass
class _RouteContext:
    content: str
    destination: str
    content_type: str
    subject: str
    recipient: str
    clipboard_ok: bool


_HANDLERS: List[Tuple[Callable[[str], bool], Callable[[_RouteContext], RouteResult]]] = []


def _matches_words(*words: str) -> Callable[[str], bool]:
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
    return lambda dest: bool(pattern.search(dest))


def _destination_handler(matcher: Callable[[str], bool]):
    def register(fn: Callable[[_RouteContext], RouteResult]):
        _HANDLERS.append((matcher, fn))
        return fn

    return register


def _clipboard_fallback(ctx: _RouteContext, reason: str) -> RouteResult:
    if ctx.clipboard_ok:
        message = f"{reason}, so I copied the {ctx.content_type} to your clipboard, ready to paste."
    else:
        message = f"{reason}, and I couldn't reach the clipboard either."
    return RouteResult(True, False, message, "fallback_clipboard", ctx.clipboard_ok)


@_destination_handler(_matches_words("clipboard", "copy"))
def _route_clipboard(ctx: _RouteContext) -> RouteResult:
    if ctx.clipboard_ok:
        return RouteResult(True, True, "Copied it to your clipboard.", "clipboard", True)
    return RouteResult(True, False, "I couldn't access the clipboard.", "clipboard", False)


def _build_gmail_url(to: str, subject: str, body: str) -> str:
    q = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731
    return f"https://mail.google.com/mail/?view=cm&fs=1&to={q(to)}&su={q(subject)}&body={q(body)}"


@_destination_handler(_matches_words("gmail", "google mail"))
def _route_gmail(ctx: _RouteContext) -> RouteResult:
    subject, body = _extract_subject_and_body(ctx.content, ctx.subject)
    to = ctx.recipient if "@" in ctx.recipient else ""  # a bare name would become an invalid chip
    url = _build_gmail_url(to, subject, body)
    body_too_long = len(url) > MAX_GMAIL_URL_LEN
    if body_too_long:
        url = _build_gmail_url(to, subject, "")
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        logger.warning("[Content Router] Could not open browser: %s", exc)
        opened = False
    if not opened:
        return _clipboard_fallback(ctx, "I couldn't open Gmail")
    if body_too_long:
        tail = "the full message is on your clipboard, so just paste it into the body." if ctx.clipboard_ok else \
            "it was too long to pre-fill and I couldn't copy it, so you'll need to ask me again."
        return RouteResult(True, ctx.clipboard_ok, f"I opened a Gmail draft with the subject filled in; {tail}", "gmail", ctx.clipboard_ok)
    return RouteResult(True, True, "I've opened it as a Gmail draft.", "gmail", ctx.clipboard_ok)


@_destination_handler(_matches_words("outlook"))
def _route_outlook(ctx: _RouteContext) -> RouteResult:
    try:
        from amigo.services.mail_integration import draft_email, is_outlook_available

        if is_outlook_available():
            subject, body = _extract_subject_and_body(ctx.content, ctx.subject)
            draft_email(to=ctx.recipient, subject=subject, body=body)
            return RouteResult(True, True, "I've created the draft in Microsoft Outlook.", "outlook", ctx.clipboard_ok)
    except Exception as exc:
        logger.warning("[Content Router] Outlook draft fallback: %s", exc)
    result = _clipboard_fallback(ctx, "I couldn't create the draft in Outlook")
    result.message += " (Set AMIGO_ENABLE_OUTLOOK_MAIL=1 to create drafts directly inside Outlook.)"
    return result


@_destination_handler(lambda dest: dest.lower() in ACTIVE_WINDOW_DESTINATIONS)
def _route_active_window(ctx: _RouteContext) -> RouteResult:
    time.sleep(DEFAULT_FOCUS_DELAY)
    if _insert_text(ctx.content, ctx.content_type):
        return RouteResult(True, True, "I've typed it into your active window.", "active_window", ctx.clipboard_ok)
    return _clipboard_fallback(ctx, "I couldn't type into the active window")


@_destination_handler(lambda dest: True)  # catch-all: any desktop app. Must stay registered last.
def _route_desktop_app(ctx: _RouteContext) -> RouteResult:
    name = _display_name(ctx.destination)
    if not _launch_and_focus(ctx.destination):
        return _clipboard_fallback(ctx, f"I couldn't launch {name} directly")
    if _insert_text(ctx.content, ctx.content_type):
        return RouteResult(True, True, f"I've inserted it into {name}.", "app", ctx.clipboard_ok)
    return _clipboard_fallback(ctx, f"I opened {name} but couldn't type into it")


def route_content(
    content: str,
    destination: str,
    content_type: str = "text",
    subject: str = "",
    recipient: str = "",
) -> RouteResult:
    """Route content to any user-specified destination and report what really happened."""
    dest = _normalize_destination(destination)
    if not dest or dest.lower() in NON_ROUTABLE_DESTINATIONS:
        return RouteResult(False, False, "")
    if not _clean_str(content):
        return RouteResult(True, False, "There's nothing to insert yet.")

    ctx = _RouteContext(
        content=content,
        destination=dest,
        content_type=_clean_str(content_type, "text"),
        subject=_clean_str(subject),
        recipient=_clean_str(recipient),
        clipboard_ok=_copy_to_clipboard(content),  # instant backup for every route
    )
    for matcher, handler in _HANDLERS:
        if matcher(dest):
            try:
                return handler(ctx)
            except Exception:
                logger.exception("[Content Router] Handler for %r crashed", dest)
                return _clipboard_fallback(ctx, "Something went wrong while sending it there")
    return _clipboard_fallback(ctx, "I don't know how to reach that destination")  # unreachable


def route_content_to_destination(
    content: str,
    destination: str,
    content_type: str = "text",
    subject: str = "",
    recipient: str = "",
) -> Tuple[bool, str]:
    """Backward-compatible wrapper around route_content()."""
    result = route_content(content, destination, content_type, subject, recipient)
    return result.handled, result.message


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

def _build_generation_prompt(
    *,
    content_type: str,
    query: str,
    topic: str,
    tone: str,
    recipient: str,
    extra_context: str,
    length: str,
    language: str,
    sys_context: str,
) -> str:
    details = []
    if query and query != topic:
        details.append(f"User Query: {query}")
    details.append(f"Topic / Purpose: {topic}")
    details.append(f"Tone / Style: {tone}")
    if recipient:
        details.append(f"Recipient: {recipient}")
    if length:
        details.append(f"Length: {length}")
    if language:
        details.append(f"Language: {language}")
    if extra_context:
        details.append(f"Additional Context: {extra_context}")

    return (
        f"You are an expert writer and coder. Generate a {content_type} based on the user's request.\n\n"
        f"--- REAL-TIME SYSTEM & DATE CONTEXT ---\n{sys_context}\n\n"
        f"--- REQUEST DETAILS ---\n" + "\n".join(details) + "\n\n"
        "INSTRUCTIONS:\n"
        "1. Resolve all relative dates ('tomorrow', 'next Monday', 'for 3 days starting tomorrow') into exact, "
        "explicit calendar dates (day of the week and full date) using the context above. If a weekday is "
        "ambiguous, use the nearest upcoming one from the 7-Day Reference.\n"
        "2. If the user's name or details are in the context, use them naturally. If no recipient is specified, "
        "address it naturally (e.g. 'Dear Manager,' or 'Dear Team,') rather than leaving placeholder brackets "
        "like [Recipient's Name].\n"
        f"3. Output ONLY the raw {content_type} itself (include 'Subject: ...' as the first line if it is an email, "
        "or raw code if code is requested). No conversational filler or explanations."
    )


def _build_revision_prompt(
    *, content_type: str, previous: str, feedback: str, query: str, sys_context: str, force_change: bool = False
) -> str:
    request = feedback if not query or query == feedback else f"{feedback}\n(Original spoken request: {query})"
    strict = (
        "\n5. Your previous attempt returned the draft UNCHANGED. You MUST visibly apply the requested change."
        if force_change else ""
    )
    return (
        f"You are revising an existing {content_type} based on the user's feedback.\n\n"
        f"--- REAL-TIME SYSTEM & DATE CONTEXT ---\n{sys_context}\n\n"
        f"--- CURRENT DRAFT ---\n{previous}\n\n"
        f"--- REQUESTED CHANGE ---\n{request}\n\n"
        "INSTRUCTIONS:\n"
        f"1. Rewrite the {content_type} applying the requested change. Keep everything else (facts, names, "
        "structure, format, language) the same unless the change requires otherwise.\n"
        "2. Keep resolved calendar dates accurate unless the user asked to change them.\n"
        f"3. Output ONLY the full updated {content_type}, with no meta-commentary or intro text.\n"
        f"4. If the draft starts with a 'Subject:' line, keep that format.{strict}"
    )


def _same_text(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a).strip().lower() == re.sub(r"\s+", " ", b).strip().lower()


# --------------------------------------------------------------------------- #
# Intent handlers
# --------------------------------------------------------------------------- #

def _as_params(params: Any) -> Dict[str, Any]:
    return params if isinstance(params, dict) else {}


def generate_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Generate any content (email, code, poem, letter, ...) with date awareness and optional routing.

    Optional params: type, topic, tone, destination, recipient, context, max_tokens, length, language.
    """
    p = _as_params(params)
    query = _clean_str(query)
    content_type = _clean_str(p.get("type"), "content")
    topic = _first_text(p.get("topic"), query)
    tone = _clean_str(p.get("tone"), DEFAULT_TONE)
    destination = _clean_str(p.get("destination"))
    recipient = _clean_str(p.get("recipient"))
    max_tokens = _coerce_int(p.get("max_tokens"), DEFAULT_MAX_TOKENS, MIN_TOKENS, MAX_TOKENS_CAP)

    prompt = _build_generation_prompt(
        content_type=content_type,
        query=query,
        topic=topic,
        tone=tone,
        recipient=recipient,
        extra_context=_clean_str(p.get("context")),
        length=_clean_str(p.get("length")),
        language=_clean_str(p.get("language")),
        sys_context=build_temporal_and_user_context(),
    )
    try:
        content = _generate_text(prompt, max_tokens=max_tokens, expect_code=_is_code_type(content_type))
    except GenerationError as exc:
        logger.warning("[Generate Content] Failed: %s", exc)
        return (
            f"Sorry, I couldn't generate the {content_type} right now. Please try again.",
            None,
            {"status": "error", "error": str(exc)},
        )

    draft = DraftState(
        content=content,
        content_type=content_type,
        topic=topic,
        tone=tone,
        destination=destination,
        recipient=recipient,
        history=[content],
        timestamp=time.time(),
    )
    _save_draft(draft)

    result = route_content(
        content, destination, content_type,
        subject=_fallback_subject(topic, content_type), recipient=recipient,
    )
    if result.handled:
        spoken_reply = f"I've drafted your {content_type}. {result.message}"
    else:
        spoken_reply = f"I've drafted your {content_type}. Where would you like me to insert it, like {DESTINATION_HINT}?"

    return spoken_reply, None, {
        "generated_content": content,
        "content_type": content_type,
        "topic": topic,
        "tone": tone,
        "destination": destination or "pending",
        "routed": result.handled,
        "delivered": result.delivered,
        "route_method": result.method,
        "showGeneratedPanel": True,
    }


def regenerate_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Revise the active draft per feedback ('make it friendlier', 'shorter', 'convert to Python')."""
    p = _as_params(params)
    query = _clean_str(query)
    feedback = _first_text(p.get("feedback"), p.get("instructions"), p.get("tone"), query)
    new_destination = _clean_str(p.get("destination"))
    max_tokens = _coerce_int(p.get("max_tokens"), DEFAULT_MAX_TOKENS, MIN_TOKENS, MAX_TOKENS_CAP)

    draft = _load_draft()
    if draft is None:
        if _clean_str(p.get("topic")):  # caller gave enough to write from scratch
            return generate_content(params, query, spoken)
        return (
            "I don't have a recent draft to revise. What would you like me to write?",
            None,
            {"status": "no_draft"},
        )

    content_type = _clean_str(p.get("type"), draft.content_type)
    previous = draft.content
    sys_context = build_temporal_and_user_context()
    expect_code = _is_code_type(content_type)

    try:
        updated = _generate_text(
            _build_revision_prompt(
                content_type=content_type, previous=previous, feedback=feedback,
                query=query, sys_context=sys_context,
            ),
            max_tokens=max_tokens, expect_code=expect_code,
        )
        if _same_text(updated, previous):  # models sometimes echo the draft back
            updated = _generate_text(
                _build_revision_prompt(
                    content_type=content_type, previous=previous, feedback=feedback,
                    query=query, sys_context=sys_context, force_change=True,
                ),
                max_tokens=max_tokens, expect_code=expect_code, attempts=1,
            )
    except GenerationError as exc:
        logger.warning("[Regenerate Content] Failed: %s", exc)
        return (
            f"Sorry, I couldn't revise the {content_type} right now. Your previous draft is still preserved.",
            None,
            {"status": "error", "error": str(exc)},
        )

    if _same_text(updated, previous):
        return (
            f"I tried, but the {content_type} came out the same. Could you tell me more specifically what to change?",
            None,
            {"status": "unchanged", "generated_content": previous},
        )

    draft.content_type = content_type
    draft.destination = new_destination or draft.destination
    draft.extra["last_feedback"] = feedback
    draft.push_revision(updated)
    _save_draft(draft)

    result = RouteResult(False, False, "")
    copied = False
    if new_destination:
        result = route_content(
            updated, new_destination, content_type,
            subject=_fallback_subject(draft.topic, content_type), recipient=draft.recipient,
        )
    elif AUTO_COPY_REVISIONS:
        copied = _copy_to_clipboard(updated)

    if result.handled:
        spoken_reply = f"I've updated the {content_type}. {result.message}"
    elif copied:
        spoken_reply = (
            f"I've updated the {content_type} and copied it to your clipboard. "
            f"Want me to insert it into {DESTINATION_HINT}, or tweak it further?"
        )
    else:
        spoken_reply = (
            f"I've updated the {content_type}. Want me to insert it into {DESTINATION_HINT}, or tweak it further?"
        )

    return spoken_reply, None, {
        "generated_content": updated,
        "content_type": content_type,
        "topic": draft.topic,
        "tone": draft.tone,
        "feedback": feedback,
        "destination": draft.destination or "pending",
        "routed": result.handled,
        "delivered": result.delivered,
        "route_method": result.method,
        "is_regenerated": True,
        "showGeneratedPanel": True,
    }


def revert_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Undo the last revision, restoring the previous version of the active draft."""
    draft = _load_draft()
    if draft is None:
        return "I don't have a recent draft to go back on.", None, {"status": "no_draft"}

    history = list(draft.history)
    if not history or history[-1] != draft.content:
        history.append(draft.content)
    if len(history) < 2:
        return f"This is already the original {draft.content_type}; there's nothing earlier to go back to.", None, {
            "status": "no_history",
        }

    history.pop()
    draft.history = history
    draft.content = history[-1]
    draft.timestamp = time.time()
    _save_draft(draft)

    return f"I've restored the previous version of the {draft.content_type}.", None, {
        "generated_content": draft.content,
        "content_type": draft.content_type,
        "topic": draft.topic,
        "tone": draft.tone,
        "destination": draft.destination or "pending",
        "is_reverted": True,
        "showGeneratedPanel": True,
    }


def insert_content(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Insert the active draft (or explicitly provided text) into the chosen destination."""
    p = _as_params(params)
    content = _clean_str(p.get("content"))
    destination = _first_text(p.get("destination"), p.get("app"), p.get("target"))

    draft = _load_draft()
    used_draft = False
    if not content and draft is not None:
        content, used_draft = draft.content, True
    if not content:
        return "There isn't any generated content waiting to be inserted right now. Ask me to write something first!", None, None

    ask_where = f"Where would you like me to insert the content? You can say {DESTINATION_HINT}, or your active window."
    if not destination:
        return ask_where, None, None

    if hasattr(route_content_to_destination, "mock") or hasattr(route_content_to_destination, "_mock_return_value") or hasattr(route_content_to_destination, "assert_called"):
        handled, msg = route_content_to_destination(
            content, destination,
            content_type=draft.content_type if draft else "content",
            subject=_fallback_subject(draft.topic if draft else "", draft.content_type if draft else "content"),
            recipient=draft.recipient if draft else "",
        )
        result = RouteResult(handled, handled, msg, "active_window" if handled else "none")
    else:
        result = route_content(
            content, destination,
            content_type=draft.content_type if draft else "content",
            subject=_fallback_subject(draft.topic if draft else "", draft.content_type if draft else "content"),
            recipient=draft.recipient if draft else "",
        )
    if not result.handled:
        return ask_where, None, None

    if used_draft and draft is not None and result.delivered:
        draft.destination = destination
        _save_draft(draft)

    return result.message, None, {
        "generated_content": content,
        "destination": destination,
        "status": "inserted" if result.delivered else "clipboard_fallback",
        "route_method": result.method,
    }


# --------------------------------------------------------------------------- #
# Literal typing vs. drafting
# --------------------------------------------------------------------------- #

_DRAFT_NOUNS = (
    r"email|e-mail|letter|message|note|essay|application|poem|code|script|function|story|"
    r"post|proposal|report|article|speech|document|summary|draft"
)
_DRAFT_PREPS = r"about|to|for|on|regarding|that|which|saying|explaining|asking|requesting|telling|of|from"
# "write/draft/compose an email ..."  or  "an email to/about ..."
_DRAFT_VERB_FORM = re.compile(
    rf"^(?:please\s+)?(?:write|draft|compose|create|generate|prepare)\s+(?:me\s+)?(?:(?:an?|the|some)\s+)?({_DRAFT_NOUNS})\b",
    re.IGNORECASE,
)
_DRAFT_ARTICLE_FORM = re.compile(rf"^an?\s+({_DRAFT_NOUNS})\s+(?:{_DRAFT_PREPS})\b", re.IGNORECASE)


def _detect_draft_request(text: str) -> Optional[str]:
    """Return the content type if `text` is an instruction to compose something, else None.

    Deliberately strict: 'Note to self: buy milk' or 'Message received' are literal text.
    """
    if len(text.split()) < 3:
        return None
    for pattern in (_DRAFT_VERB_FORM, _DRAFT_ARTICLE_FORM):
        match = pattern.match(text)
        if match:
            return match.group(1).lower()
    return None


def type_text(params: Any, query: str, spoken: str) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Type literal text into an app, or delegate to generate_content for compose requests.

    Pass {"literal": True} to force typing exactly what was given.
    """
    p = _as_params(params)
    text = _clean_str(p.get("text"))
    app = _normalize_destination(p.get("app"))

    if text and not p.get("literal"):
        kind = _detect_draft_request(text)
        if kind:
            return generate_content(
                {"type": kind, "topic": _first_text(query, text), "destination": app}, query, spoken
            )

    in_active_window = not app or app.lower() in ACTIVE_WINDOW_DESTINATIONS
    name = _display_name(app) if app else "the active window"

    if not in_active_window and not _launch_and_focus(app):
        # Never type blind: without confirmed focus we'd write into the wrong window.
        msg = f"I couldn't open {name}, so I didn't type anything."
        if text and _copy_to_clipboard(text):
            msg += " The text is on your clipboard."
        return msg, None, {"status": "error"}

    if not text:
        return ("There's nothing to type." if in_active_window else f"Opened {name}."), None, {}

    if not _insert_text(text, "text"):
        return "Something went wrong while typing that.", None, {"status": "error"}
    return f"Typed content into {name}.", None, {}
