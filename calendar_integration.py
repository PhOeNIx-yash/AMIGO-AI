"""
Outlook Calendar Integration for Amigo Voice Assistant.
Reads calendar events from Microsoft Outlook via win32com COM API.
All data stays local — read-only access.
"""

import datetime
import logging
import threading

logger = logging.getLogger("amigo.calendar_integration")

_outlook_ns = None
_cal_lock = threading.Lock()


def _get_namespace():
    """Lazy-connect to Outlook MAPI namespace."""
    global _outlook_ns
    if _outlook_ns is not None:
        return _outlook_ns

    with _cal_lock:
        if _outlook_ns is not None:
            return _outlook_ns
        try:
            import pythoncom
            pythoncom.CoInitialize()
            import win32com.client
            _outlook_ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            logger.info("[Calendar] Connected to Outlook Calendar.")
            return _outlook_ns
        except Exception as e:
            logger.warning("[Calendar] Outlook not available: %s", e)
            return None


def _format_dt(dt) -> str:
    """Format Outlook datetime to readable string."""
    try:
        if isinstance(dt, datetime.datetime):
            return dt.strftime("%Y-%m-%d %I:%M %p")
        if hasattr(dt, "Format"):
            return str(dt)
        return str(dt)
    except Exception:
        return str(dt)


def _format_iso(dt) -> str:
    """Format Outlook datetime to ISO string."""
    try:
        if hasattr(dt, "isoformat"):
            return dt.isoformat()
        return str(dt)
    except Exception:
        return ""


def is_outlook_available() -> bool:
    """Check if Outlook calendar is accessible."""
    return _get_namespace() is not None


def get_todays_events() -> list[dict]:
    """Get all calendar events for today."""
    ns = _get_namespace()
    if not ns:
        return []

    try:
        calendar = ns.GetDefaultFolder(9)  # olFolderCalendar
        items = calendar.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        today = datetime.date.today()
        start_str = today.strftime("%m/%d/%Y 12:00 AM")
        end_str = (today + datetime.timedelta(days=1)).strftime("%m/%d/%Y 12:00 AM")
        restriction = f"[Start] >= '{start_str}' AND [Start] < '{end_str}'"
        filtered = items.Restrict(restriction)

        events = []
        for item in filtered:
            try:
                events.append({
                    "subject": item.Subject or "(No Title)",
                    "start": _format_dt(item.Start),
                    "end": _format_dt(item.End),
                    "start_iso": _format_iso(item.Start),
                    "end_iso": _format_iso(item.End),
                    "location": item.Location or "",
                    "body": (item.Body or "")[:300].strip(),
                    "all_day": bool(item.AllDayEvent),
                    "is_recurring": bool(item.IsRecurring),
                })
            except Exception:
                continue

        return events
    except Exception as e:
        logger.error("[Calendar] Error fetching today's events: %s", e)
        return []


def get_upcoming_events(days: int = 7) -> list[dict]:
    """Get calendar events for the next N days."""
    ns = _get_namespace()
    if not ns:
        return []

    try:
        calendar = ns.GetDefaultFolder(9)
        items = calendar.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        now = datetime.datetime.now()
        end = now + datetime.timedelta(days=days)
        start_str = now.strftime("%m/%d/%Y %I:%M %p")
        end_str = end.strftime("%m/%d/%Y %I:%M %p")
        restriction = f"[Start] >= '{start_str}' AND [Start] <= '{end_str}'"
        filtered = items.Restrict(restriction)

        events = []
        for item in filtered:
            try:
                events.append({
                    "subject": item.Subject or "(No Title)",
                    "start": _format_dt(item.Start),
                    "end": _format_dt(item.End),
                    "start_iso": _format_iso(item.Start),
                    "end_iso": _format_iso(item.End),
                    "location": item.Location or "",
                    "body": (item.Body or "")[:300].strip(),
                    "all_day": bool(item.AllDayEvent),
                    "is_recurring": bool(item.IsRecurring),
                })
            except Exception:
                continue

        return events
    except Exception as e:
        logger.error("[Calendar] Error fetching upcoming events: %s", e)
        return []


def get_next_event() -> dict | None:
    """Get the very next upcoming event."""
    events = get_upcoming_events(days=7)
    return events[0] if events else None


def search_events(query: str, days: int = 30) -> list[dict]:
    """Search calendar events by keyword in subject/location/body."""
    if not query:
        return []

    all_events = get_upcoming_events(days=days)
    q_lower = query.lower()

    return [
        e for e in all_events
        if q_lower in e["subject"].lower()
        or q_lower in e.get("location", "").lower()
        or q_lower in e.get("body", "").lower()
    ]


def get_calendar_summary_text() -> str:
    """Return a text summary of today's events for the LLM to process."""
    events = get_todays_events()
    if not events:
        return "You have no events scheduled for today."

    lines = [f"You have {len(events)} event{'s' if len(events) > 1 else ''} today:"]
    for i, e in enumerate(events, 1):
        time_str = f"{e['start']} - {e['end']}"
        loc = f" at {e['location']}" if e['location'] else ""
        lines.append(f"  {i}. {e['subject']}{loc} ({time_str})")

    return "\n".join(lines)


def get_upcoming_summary_text(days: int = 7) -> str:
    """Return a text summary of upcoming events for the LLM."""
    events = get_upcoming_events(days=days)
    if not events:
        return f"You have no events scheduled for the next {days} days."

    lines = [f"You have {len(events)} event{'s' if len(events) > 1 else ''} in the next {days} days:"]
    for i, e in enumerate(events, 1):
        loc = f" at {e['location']}" if e['location'] else ""
        lines.append(f"  {i}. {e['subject']}{loc} — {e['start']}")

    return "\n".join(lines)


def index_events_to_rag(rag_engine, days: int = 90) -> int:
    """Bulk-index calendar events into the RAG vector store."""
    events = get_upcoming_events(days=days)
    indexed = 0
    for e in events:
        try:
            rag_engine.index_calendar_event(
                subject=e["subject"],
                start=e.get("start_iso", e["start"]),
                end=e.get("end_iso", e["end"]),
                location=e.get("location", ""),
                body=e.get("body", ""),
            )
            indexed += 1
        except Exception:
            continue
    logger.info("[Calendar] Indexed %d events into RAG.", indexed)
    return indexed
