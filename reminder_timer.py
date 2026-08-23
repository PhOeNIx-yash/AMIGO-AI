import datetime
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

import dateutil.parser

logger = logging.getLogger("amigo.reminder_timer")

# Store persistence file dynamically in the same directory as this module
REMINDERS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "amigo_reminders.json"
)

_speak_callback: Optional[Callable[[str], None]] = None
_broadcast_callback: Optional[Callable[[str, dict], None]] = None

_active_timers: Dict[str, dict] = {}
_scheduled_reminders: List[dict] = []
_reminders_lock = threading.Lock()
_scheduler_running = False
_scheduler_thread: Optional[threading.Thread] = None


# ============================================================================
# Dynamic Windows Desktop Notification Helper
# ============================================================================
def show_desktop_notification(title: str, message: str) -> None:
    """Shows a native Windows desktop toast notification asynchronously."""
    def _notify():
        try:
            # Escape strings safely for PowerShell
            safe_title = title.replace("'", "''").replace('"', '`"')
            safe_msg = message.replace("'", "''").replace('"', '`"')
            
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$textNodes = $template.GetElementsByTagName('text'); "
                f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null; "
                f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_msg}')) > $null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Amigo AI Assistant').Show($toast);"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"[Notification] Desktop toast note: {e}")

    threading.Thread(target=_notify, daemon=True).start()


# ============================================================================
# Dynamic Time Parsing Helpers (Zero Hardcoding)
# ============================================================================
WORD_TO_NUM = {
    "zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "half": 0.5, "quarter": 0.25,
}

def parse_relative_seconds(duration_str: str) -> Optional[float]:
    """
    Dynamically extracts total seconds from natural duration phrases like:
    '10m', '10 minutes', 'one minute', 'half an hour', '1 hour 30 mins', '45 seconds', '2.5 hrs', '30s'.
    """
    if not duration_str:
        return None

    text = str(duration_str).lower().strip()

    # Handle idiomatic half/quarter phrases
    text = re.sub(r"\bhalf\s+(?:an?\s+)?hours?\b", "30 minutes", text)
    text = re.sub(r"\bquarter\s+(?:of\s+an?\s+)?hours?\b", "15 minutes", text)

    # Replace word numbers with digits
    for word, num in WORD_TO_NUM.items():
        text = re.sub(rf"\b{word}\b", str(num), text)

    total_seconds = 0.0
    matched = False

    # Check for combined hour/minute/second patterns
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", 3600.0),
        (r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b", 60.0),
        (r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", 1.0),
    ]

    for pat, mult in patterns:
        for match in re.finditer(pat, text):
            try:
                val = float(match.group(1))
                total_seconds += val * mult
                matched = True
            except ValueError:
                pass

    if matched and total_seconds > 0:
        return total_seconds

    # Fallback for plain integers (assumes minutes if >=1 and <=120, else seconds)
    cleaned_num = re.sub(r"[^\d.]", "", text)
    if cleaned_num:
        try:
            num = float(cleaned_num)
            if num > 0:
                return float(num * 60 if num <= 120 else num)
        except ValueError:
            pass

    return None


def parse_target_datetime(time_str: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """
    Dynamically parses a natural time string into a concrete future datetime.
    Handles 'in 10 minutes', '5:30 PM', 'tomorrow at 10 AM', 'tonight at 8', etc.
    """
    if not time_str:
        return None

    if now is None:
        now = datetime.datetime.now()

    text = str(time_str).strip()

    # 1. Check if it's a relative offset ("in X minutes/hours")
    rel_match = re.search(r"\bin\s+(.+)$", text, re.IGNORECASE)
    if rel_match:
        dur = parse_relative_seconds(rel_match.group(1))
        if dur:
            return now + datetime.timedelta(seconds=dur)

    # 2. Check if the whole string is a duration
    dur = parse_relative_seconds(text)
    if dur and not re.search(r"\b(?:at|am|pm|o'?clock)\b", text, re.IGNORECASE):
        return now + datetime.timedelta(seconds=dur)

    # 3. Dynamic datetime parsing via dateutil
    # Handle "tomorrow at X"
    is_tomorrow = bool(re.search(r"\btomorrow\b", text, re.IGNORECASE))
    clean_text = re.sub(r"\b(?:tomorrow|today|tonight|at|on|for)\b", "", text, flags=re.IGNORECASE).strip()

    try:
        parsed_dt = dateutil.parser.parse(clean_text, default=now, fuzzy=True)
        if is_tomorrow:
            parsed_dt = parsed_dt + datetime.timedelta(days=1)
        elif parsed_dt <= now:
            # If the parsed clock time is in the past today, roll over to tomorrow
            if (now - parsed_dt).total_seconds() > 60:
                parsed_dt = parsed_dt + datetime.timedelta(days=1)

        return parsed_dt
    except Exception:
        pass

    return None


# ============================================================================
# Persistence (Local JSON File)
# ============================================================================
def load_reminders() -> List[dict]:
    """Load scheduled reminders from disk."""
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug(f"Error loading reminders from disk: {e}")
    return []


_disk_save_lock = threading.Lock()

def save_reminders(reminders: List[dict]) -> None:
    """Save active reminders to disk in a thread-safe manner."""
    with _disk_save_lock:
        try:
            with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(reminders, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving reminders: {e}")


# ============================================================================
# Background Scheduler Loop
# ============================================================================
def _run_scheduler_loop() -> None:
    """Background thread checking active reminders and timers every second."""
    global _scheduler_running, _scheduled_reminders
    logger.info("[Scheduler] Reminder & Timer scheduler started.")
    
    while _scheduler_running:
        try:
            now = datetime.datetime.now()
            triggered: List[dict] = []

            with _reminders_lock:
                remaining: List[dict] = []
                for rem in _scheduled_reminders:
                    try:
                        target_dt = datetime.datetime.fromisoformat(rem["target_time"])
                        if now >= target_dt:
                            triggered.append(rem)
                        else:
                            remaining.append(rem)
                    except Exception:
                        pass
                
                if triggered:
                    _scheduled_reminders = remaining
                    save_reminders(_scheduled_reminders)

            for rem in triggered:
                _trigger_reminder(rem)

            time.sleep(1.0)
        except Exception as e:
            logger.debug(f"[Scheduler Loop] Exception: {e}")
            time.sleep(2.0)


def _trigger_reminder(rem: dict) -> None:
    """Dispatches audio announcement and desktop toast for an expired reminder."""
    msg = rem.get("message", "Reminder alert!")
    alert_text = f"Reminder: {msg}"
    print(f"\n[REMINDER ALERT] {alert_text}", flush=True)

    show_desktop_notification("Amigo Reminder", msg)

    if _broadcast_callback:
        try:
            _broadcast_callback("reminder_triggered", {"id": rem.get("id"), "message": msg})
        except Exception:
            pass

    if _speak_callback:
        try:
            _speak_callback(alert_text)
        except Exception as e:
            logger.debug(f"[Reminder Speak] {e}")


def _trigger_timer(timer_id: str, label: str, duration_sec: float) -> None:
    """Dispatches audio alert and desktop toast when a countdown timer finishes."""
    with _reminders_lock:
        if timer_id in _active_timers:
            del _active_timers[timer_id]

    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)
    time_desc = []
    if mins > 0:
        time_desc.append(f"{mins} minute{'s' if mins > 1 else ''}")
    if secs > 0:
        time_desc.append(f"{secs} second{'s' if secs > 1 else ''}")
    dur_str = " and ".join(time_desc) or f"{int(duration_sec)} seconds"

    clean_label = label.strip() if (label and label.strip().lower() not in ("timer", "countdown", "stopwatch") and not parse_relative_seconds(label)) else ""
    label_str = f" for {clean_label}" if clean_label else ""
    alert_text = f"Your {dur_str} timer{label_str} is up!"
    print(f"\n[TIMER ALERT] {alert_text}", flush=True)

    show_desktop_notification("Amigo Timer", alert_text)

    if _broadcast_callback:
        try:
            _broadcast_callback("timer_triggered", {"id": timer_id, "label": clean_label, "duration": duration_sec})
        except Exception:
            pass

    if _speak_callback:
        try:
            _speak_callback(alert_text)
        except Exception as e:
            logger.debug(f"[Timer Speak] {e}")


def init_reminders(
    speak_callback: Optional[Callable[[str], None]] = None,
    broadcast_callback: Optional[Callable[[str, dict], None]] = None,
) -> None:
    """Initializes the background scheduler and restores pending reminders from disk."""
    global _speak_callback, _broadcast_callback, _scheduler_running, _scheduler_thread, _scheduled_reminders
    _speak_callback = speak_callback
    _broadcast_callback = broadcast_callback

    with _reminders_lock:
        stored = load_reminders()
        now = datetime.datetime.now()
        _scheduled_reminders = [
            r for r in stored
            if datetime.datetime.fromisoformat(r["target_time"]) > now - datetime.timedelta(hours=24)
        ]

    if not _scheduler_running:
        _scheduler_running = True
        _scheduler_thread = threading.Thread(target=_run_scheduler_loop, daemon=True)
        _scheduler_thread.start()


def handle_set_timer(params: dict, query: str = "") -> str:
    """
    Creates and starts an active countdown timer.
    Params: {"duration": "10s", "label": "tea"}
    """
    label = str(params.get("label") or "").strip()
    if label.lower() in ("timer", "countdown", "stopwatch", "a timer", "the timer"):
        label = ""

    # 1. Resolve duration from params
    dur_sec = None
    if params.get("duration") or params.get("seconds"):
        dur_sec = parse_relative_seconds(str(params.get("duration") or params.get("seconds")))

    # 2. Semantic slot check: If 'label' parses as a duration expression, treat it as duration and clear label
    if label:
        label_as_sec = parse_relative_seconds(label)
        if label_as_sec and label_as_sec > 0:
            if not dur_sec or dur_sec <= 0:
                dur_sec = label_as_sec
            label = ""

    # 3. If still missing, parse duration from user query
    if (not dur_sec or dur_sec <= 0) and query:
        dur_sec = parse_relative_seconds(str(query))

    if not dur_sec or dur_sec <= 0:
        return "Please specify a duration for the timer, such as 1 minute or 30 seconds."

    timer_id = str(uuid.uuid4())[:8]
    now = datetime.datetime.now()
    end_time = now + datetime.timedelta(seconds=dur_sec)

    mins = int(dur_sec // 60)
    secs = int(dur_sec % 60)
    parts = []
    if mins > 0:
        parts.append(f"{mins} minute{'s' if mins > 1 else ''}")
    if secs > 0:
        parts.append(f"{secs} second{'s' if secs > 1 else ''}")
    spoken_dur = " and ".join(parts) or f"{int(dur_sec)} seconds"

    def _worker():
        time.sleep(dur_sec)
        _trigger_timer(timer_id, label, dur_sec)

    threading.Thread(target=_worker, daemon=True).start()

    with _reminders_lock:
        _active_timers[timer_id] = {
            "id": timer_id,
            "label": label,
            "duration_sec": dur_sec,
            "start_time": now.isoformat(),
            "end_time": end_time.isoformat(),
        }

    # Only append custom labels (e.g. "for tea" or "for focus"), not duration phrases or generic keywords
    clean_label = ""
    if label and label.lower() not in ("timer", "countdown", "stopwatch") and not parse_relative_seconds(label):
        clean_label = f" for {label}"

    return f"Timer set for {spoken_dur}{clean_label}."


def handle_set_reminder(params: dict, query: str = "") -> str:
    """
    Creates and schedules a persistent reminder for a future time.
    Params: {"time": "5:30 PM", "message": "call doctor"}
    """
    time_raw = params.get("time") or query
    msg = str(params.get("message") or "").strip()

    if not msg and query:
        # Extract message dynamically: "remind me to call mom at 5 PM" -> "call mom"
        m = re.search(r"\b(?:remind me (?:to |about )?)(.+?)\s+(?:at|in|on|tomorrow at)\b", query, re.IGNORECASE)
        if m:
            msg = m.group(1).strip()
        else:
            msg = "Reminder"

    is_alarm = bool(re.search(r"\b(?:alarm|wake me up)\b", query, re.IGNORECASE))

    target_dt = parse_target_datetime(str(time_raw))
    if not target_dt:
        if is_alarm:
            return "Please specify a time for the alarm, like 7:30 AM or in 30 minutes."
        return "Please specify when you would like to be reminded, like in 30 minutes or at 5 PM."

    rem_id = str(uuid.uuid4())[:8]
    item = {
        "id": rem_id,
        "message": msg or ("Alarm" if is_alarm else "Reminder"),
        "created_at": datetime.datetime.now().isoformat(),
        "target_time": target_dt.isoformat(),
        "is_alarm": is_alarm,
    }

    with _reminders_lock:
        _scheduled_reminders.append(item)
        save_reminders(_scheduled_reminders)

    time_formatted = target_dt.strftime("%I:%M %p")
    if target_dt.date() > datetime.date.today():
        date_str = target_dt.strftime("%A, %b %d at ")
    else:
        date_str = ""

    if is_alarm:
        return f"Alarm set for {date_str}{time_formatted}."

    # Check if query had a relative phrase like "in 5 seconds" or "in 10 minutes"
    rel_phrase_m = re.search(r"\bin\s+(\d+\s*(?:sec|second|min|minute|hr|hour)s?)\b", query or time_raw, re.IGNORECASE)
    if rel_phrase_m:
        return f"I will remind you to {msg} in {rel_phrase_m.group(1)}."

    return f"I will remind you to {msg} {date_str}at {time_formatted}."


def handle_list_reminders() -> str:
    """Returns a natural spoken summary of all active timers and pending reminders."""
    with _reminders_lock:
        active_t = list(_active_timers.values())
        sched_r = list(_scheduled_reminders)

    if not active_t and not sched_r:
        return "You have no active timers or reminders."

    summaries = []
    now = datetime.datetime.now()

    if active_t:
        for t in active_t:
            end_dt = datetime.datetime.fromisoformat(t["end_time"])
            remaining = max(0, int((end_dt - now).total_seconds()))
            rem_m = remaining // 60
            rem_s = remaining % 60
            lbl = f" for {t['label']}" if t['label'] else ""
            summaries.append(f"A timer{lbl} with {rem_m}m {rem_s}s remaining")

    if sched_r:
        for r in sched_r:
            tgt_dt = datetime.datetime.fromisoformat(r["target_time"])
            time_str = tgt_dt.strftime("%I:%M %p")
            if tgt_dt.date() > datetime.date.today():
                time_str = tgt_dt.strftime("%A at %I:%M %p")
            summaries.append(f"Reminder to {r['message']} at {time_str}")

    return "Here are your active schedules: " + ", ".join(summaries) + "."


def handle_cancel_reminder(params: dict = None) -> str:
    """Cancels active timers and removes scheduled reminders."""
    global _active_timers, _scheduled_reminders
    with _reminders_lock:
        count_t = len(_active_timers)
        count_r = len(_scheduled_reminders)
        _active_timers.clear()
        _scheduled_reminders.clear()
        save_reminders([])

    total = count_t + count_r
    if total > 0:
        return f"Cancelled {total} active timer{'s' if total > 1 else ''} and reminder{'s' if total > 1 else ''}."
    return "You have no active timers or reminders to cancel."


def get_active_data() -> dict:
    """Returns raw structured data for UI widgets and REST endpoints."""
    with _reminders_lock:
        return {
            "timers": list(_active_timers.values()),
            "reminders": list(_scheduled_reminders),
        }
