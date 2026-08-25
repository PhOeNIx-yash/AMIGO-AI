"""
reminder_timer.py — Simplified Timers & Reminders Module for Amigo Voice Assistant.
Clean, lightweight, and easy to maintain.
"""

import os
import re
import json
import time
import uuid
import logging
import datetime
import threading
import subprocess
from typing import Callable, Dict, List, Optional
import dateutil.parser

logger = logging.getLogger("amigo.reminder_timer")

REMINDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_reminders.json")

_speak_callback: Optional[Callable[[str], None]] = None
_broadcast_callback: Optional[Callable[[str, dict], None]] = None

_active_timers: Dict[str, dict] = {}
_scheduled_reminders: List[dict] = []
_reminders_lock = threading.Lock()
_scheduler_running = False


# ---------------------------------------------------------------------------
# Simple Desktop Notification
# ---------------------------------------------------------------------------

def show_desktop_notification(title: str, message: str) -> None:
    """Shows a native Windows notification asynchronously."""
    def _notify():
        try:
            safe_t = title.replace('"', '`"').replace("'", "''")
            safe_m = message.replace('"', '`"').replace("'", "''")
            ps = f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1); $t.GetElementsByTagName("text")[0].AppendChild($t.CreateTextNode("{safe_t}")) > $null; $t.GetElementsByTagName("text")[1].AppendChild($t.CreateTextNode("{safe_m}")) > $null; [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Amigo Assistant").Show([Windows.UI.Notifications.ToastNotification]::new($t));'
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        except Exception:
            pass
    threading.Thread(target=_notify, daemon=True).start()


# ---------------------------------------------------------------------------
# Simple Duration and Date Parsing
# ---------------------------------------------------------------------------

def parse_relative_seconds(text: str) -> Optional[float]:
    """Parses natural time expressions (e.g. '10s', '5 minutes', '1 hour 30 mins', 'half an hour')."""
    if not text:
        return None
    clean = str(text).lower().replace("an hour", "1 hour").replace("half an hour", "30 minutes").replace("a minute", "1 minute")
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", clean):
        try:
            val = float(num)
            mult = 86400 if unit.startswith("d") else (3600 if unit.startswith("h") else (60 if unit.startswith("m") else 1))
            total += val * mult
        except ValueError:
            pass
    if total > 0:
        return total

    # Raw number fallback (e.g. "10")
    num_only = re.sub(r"[^\d.]", "", clean)
    try:
        return float(num_only) if num_only else None
    except ValueError:
        return None


def parse_target_datetime(time_str: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """Parses target datetime string like '5:30 PM', 'tomorrow at 10 AM', 'in 15 minutes'."""
    if not time_str:
        return None
    now = now or datetime.datetime.now()
    text = str(time_str).strip()

    # Relative offset: "in 10 minutes"
    if m := re.search(r"\bin\s+(.+)$", text, re.IGNORECASE):
        if dur := parse_relative_seconds(m.group(1)):
            return now + datetime.timedelta(seconds=dur)

    if dur := parse_relative_seconds(text):
        if not re.search(r"\b(?:at|am|pm|o'?clock)\b", text, re.IGNORECASE):
            return now + datetime.timedelta(seconds=dur)

    is_tomorrow = "tomorrow" in text.lower()
    clean = re.sub(r"\b(?:tomorrow|today|tonight|at|on|for)\b", "", text, flags=re.IGNORECASE).strip()
    try:
        dt = dateutil.parser.parse(clean, default=now, fuzzy=True)
        if is_tomorrow:
            dt += datetime.timedelta(days=1)
        elif dt <= now and (now - dt).total_seconds() > 60:
            dt += datetime.timedelta(days=1)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Simple Persistence
# ---------------------------------------------------------------------------

def load_reminders() -> List[dict]:
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or []
        except Exception:
            pass
    return []


def save_reminders(reminders: List[dict]) -> None:
    try:
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(reminders, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scheduler Loop & Trigger Handlers
# ---------------------------------------------------------------------------

def _run_scheduler_loop() -> None:
    global _scheduled_reminders
    while _scheduler_running:
        try:
            now = datetime.datetime.now()
            triggered = []
            with _reminders_lock:
                remaining = []
                for r in _scheduled_reminders:
                    try:
                        if now >= datetime.datetime.fromisoformat(r["target_time"]):
                            triggered.append(r)
                        else:
                            remaining.append(r)
                    except Exception:
                        pass
                if triggered:
                    _scheduled_reminders = remaining
                    save_reminders(_scheduled_reminders)

            for r in triggered:
                msg = r.get("message", "Reminder!")
                alert = f"Reminder: {msg}"
                print(f"\n[REMINDER ALERT] {alert}", flush=True)
                show_desktop_notification("Amigo Reminder", msg)
                if _broadcast_callback:
                    _broadcast_callback("reminder_triggered", {"id": r.get("id"), "message": msg})
                if _speak_callback:
                    _speak_callback(alert)

            time.sleep(1.0)
        except Exception:
            time.sleep(2.0)


def init_reminders(speak_callback=None, broadcast_callback=None) -> None:
    global _speak_callback, _broadcast_callback, _scheduler_running, _scheduled_reminders
    _speak_callback = speak_callback
    _broadcast_callback = broadcast_callback
    with _reminders_lock:
        stored = load_reminders()
        now = datetime.datetime.now()
        _scheduled_reminders = [r for r in stored if datetime.datetime.fromisoformat(r["target_time"]) > now - datetime.timedelta(hours=24)]
    if not _scheduler_running:
        _scheduler_running = True
        threading.Thread(target=_run_scheduler_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# User Tool Handlers
# ---------------------------------------------------------------------------

def handle_set_timer(params: dict, query: str = "") -> str:
    """Sets a countdown timer."""
    label = str(params.get("label") or "").strip()
    dur = parse_relative_seconds(query) or parse_relative_seconds(str(params.get("duration") or params.get("seconds") or label))
    if not dur or dur <= 0:
        return "Please specify a duration for the timer, like 1 minute or 30 seconds."

    timer_id = str(uuid.uuid4())[:8]
    mins, secs = int(dur // 60), int(dur % 60)
    dur_str = f"{mins}m {secs}s" if mins and secs else (f"{mins} minutes" if mins else f"{secs} seconds")

    def _worker():
        time.sleep(dur)
        with _reminders_lock:
            _active_timers.pop(timer_id, None)
        alert = f"Your {dur_str} timer is up!"
        print(f"\n[TIMER ALERT] {alert}", flush=True)
        show_desktop_notification("Amigo Timer", alert)
        if _broadcast_callback:
            _broadcast_callback("timer_triggered", {"id": timer_id, "duration": dur})
        if _speak_callback:
            _speak_callback(alert)

    threading.Thread(target=_worker, daemon=True).start()
    with _reminders_lock:
        _active_timers[timer_id] = {
            "id": timer_id,
            "label": label if label not in ("timer", "countdown", "stopwatch") else "",
            "duration_sec": dur,
            "end_time": (datetime.datetime.now() + datetime.timedelta(seconds=dur)).isoformat(),
        }

    clean_lbl = f" for {label}" if label and label not in ("timer", "countdown", "stopwatch") and not parse_relative_seconds(label) else ""
    return f"Timer set for {dur_str}{clean_lbl}."


def handle_set_reminder(params: dict, query: str = "") -> str:
    """Schedules a persistent reminder for a future time."""
    params = params if isinstance(params, dict) else {}
    time_raw = params.get("time") or query
    msg = str(params.get("message") or "").strip()
    if not msg and query:
        if m := re.search(r"\b(?:remind me (?:to |about )?)(.+?)\s+(?:at|in|on|tomorrow at)\b", query, re.IGNORECASE):
            msg = m.group(1).strip()
    msg = msg or "Reminder"

    target_dt = parse_target_datetime(str(time_raw))
    if not target_dt:
        return "Please specify when you would like to be reminded, like in 30 minutes or at 5 PM."

    rem_id = str(uuid.uuid4())[:8]
    item = {"id": rem_id, "message": msg, "target_time": target_dt.isoformat()}

    with _reminders_lock:
        norm_msg = msg.strip().lower()
        for existing in _scheduled_reminders:
            try:
                e_time = datetime.datetime.fromisoformat(existing["target_time"])
                e_msg = str(existing.get("message", "")).strip().lower()
                if (norm_msg in e_msg or e_msg in norm_msg) and abs((e_time - target_dt).total_seconds()) <= 120:
                    return "That reminder is already scheduled."
            except Exception:
                pass

        _scheduled_reminders.append(item)
        save_reminders(_scheduled_reminders)

    time_str = target_dt.strftime("%I:%M %p")
    date_str = target_dt.strftime("%A at ") if target_dt.date() > datetime.date.today() else ""
    return f"I will remind you to {msg} {date_str}at {time_str}."


def handle_list_reminders() -> str:
    """Lists all active timers and pending reminders."""
    with _reminders_lock:
        timers = list(_active_timers.values())
        reminders = list(_scheduled_reminders)

    if not timers and not reminders:
        return "You have no active timers or reminders."

    items = []
    now = datetime.datetime.now()
    for t in timers:
        rem = max(0, int((datetime.datetime.fromisoformat(t["end_time"]) - now).total_seconds()))
        items.append(f"Timer ({rem // 60}m {rem % 60}s remaining)")
    for r in reminders:
        dt = datetime.datetime.fromisoformat(r["target_time"])
        items.append(f"Reminder to {r['message']} at {dt.strftime('%I:%M %p')}")

    return "Here are your active schedules: " + ", ".join(items) + "."


def handle_cancel_reminder(params: dict = None) -> str:
    """Cancels all active timers and reminders."""
    with _reminders_lock:
        count = len(_active_timers) + len(_scheduled_reminders)
        _active_timers.clear()
        _scheduled_reminders.clear()
        save_reminders([])
    return f"Cancelled {count} active timer(s) and reminder(s)." if count else "You have no active timers or reminders."


def get_active_data() -> dict:
    with _reminders_lock:
        return {"timers": list(_active_timers.values()), "reminders": list(_scheduled_reminders)}
