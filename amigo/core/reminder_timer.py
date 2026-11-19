"""
reminder_timer.py — Reliable, Zero-Hardcoded-Words Timers & Reminders Engine for Amigo.
- Zero hardcoded English phrase/word-replacement tables.
- Interruptible timers via threading.Event (cancelled timers never fire late).
- Targeted cancellation & rescheduling by ID or fuzzy message match (never accidentally wipes all reminders).
- Atomic JSON persistence and multi-day date formatting.
"""

import datetime
import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

import dateutil.parser

logger = logging.getLogger("amigo.reminder_timer")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REMINDERS_FILE = os.path.join(_PROJECT_ROOT, "amigo_reminders.json")
if not os.path.exists(REMINDERS_FILE) and os.path.exists(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_reminders.json")
):
    REMINDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_reminders.json")

_speak_callback: Optional[Callable[[str], None]] = None
_broadcast_callback: Optional[Callable[[str, dict], None]] = None

_active_timers: Dict[str, dict] = {}
_timer_cancel_events: Dict[str, threading.Event] = {}
_scheduled_reminders: List[dict] = []
_reminders_lock = threading.Lock()
_scheduler_running = False

_RE_DURATION_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])\w*", re.IGNORECASE)
_RE_PURE_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)?\s*$")
_RE_CLOCK_TIME = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(?:am|pm)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Desktop Notification
# ---------------------------------------------------------------------------

def show_desktop_notification(title: str, message: str) -> None:
    """Shows a native Windows toast notification asynchronously."""
    def _notify():
        try:
            safe_t = str(title).replace('"', '`"').replace("'", "''")
            safe_m = str(message).replace('"', '`"').replace("'", "''")
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1); "
                f'$t.GetElementsByTagName("text")[0].AppendChild($t.CreateTextNode("{safe_t}")) > $null; '
                f'$t.GetElementsByTagName("text")[1].AppendChild($t.CreateTextNode("{safe_m}")) > $null; '
                '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Amigo Assistant")'
                ".Show([Windows.UI.Notifications.ToastNotification]::new($t));"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
            )
        except Exception:
            pass

    threading.Thread(target=_notify, daemon=True).start()


# ---------------------------------------------------------------------------
# Structural Duration & Datetime Parsing (Zero Word Lists)
# ---------------------------------------------------------------------------

def parse_relative_seconds(text: str) -> Optional[float]:
    """
    Parses structured/unit duration strings (e.g. '10s', '5m', '1h 30m', '2d', or raw seconds '45')
    without hardcoded English word-substitution dictionaries.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    # Ignore clock timestamps like "5:30" or "5 pm" so they are not misread as durations
    if _RE_CLOCK_TIME.search(raw):
        return None

    total = 0.0
    matched = False
    for num, unit_char in _RE_DURATION_TOKEN.findall(raw):
        try:
            val = float(num)
            u = unit_char.lower()
            mult = 86400.0 if u == "d" else (3600.0 if u == "h" else (60.0 if u == "m" else 1.0))
            total += val * mult
            matched = True
        except ValueError:
            pass

    if matched and total > 0:
        return total

    if _RE_PURE_NUMBER.match(raw):
        try:
            val = float(raw)
            return val if val > 0 else None
        except ValueError:
            pass

    return None


def parse_target_datetime(time_str: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """
    Parses ISO-8601 timestamps, relative offsets ('20m', '+1h'), or clock times ('17:30', '5:30 PM')
    using dateutil without hardcoded English keyword stripping.
    """
    if not time_str:
        return None
    now = now or datetime.datetime.now()
    text = str(time_str).strip()
    if not text:
        return None

    # 1. Pure relative duration offset (e.g., "20m", "+30m", "1h 15m")
    offset_candidate = text.lstrip("+").strip()
    if dur := parse_relative_seconds(offset_candidate):
        return now + datetime.timedelta(seconds=dur)

    # 2. ISO-8601 or structured date/time parsing
    try:
        dt = dateutil.parser.isoparse(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        pass

    try:
        has_explicit_date = bool(re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}", text))
        dt = dateutil.parser.parse(text, default=now, fuzzy=True)
        # If no explicit calendar date was given and the parsed clock time has already passed today, roll to next day
        if not has_explicit_date and dt <= now and (now - dt).total_seconds() > 60:
            dt += datetime.timedelta(days=1)
        return dt
    except Exception:
        return None


def _format_duration(seconds: float) -> str:
    total = max(1, int(round(seconds)))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if hrs:
        parts.append(f"{hrs}h" if (mins or secs) else f"{hrs} hour{'s' if hrs != 1 else ''}")
    if mins:
        parts.append(f"{mins}m" if (hrs or secs) else f"{mins} minute{'s' if mins != 1 else ''}")
    if secs:
        parts.append(f"{secs}s" if (hrs or mins) else f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)


def _format_target_when(target_dt: datetime.datetime, now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now()
    time_str = target_dt.strftime("%I:%M %p").lstrip("0")
    day_diff = (target_dt.date() - now.date()).days
    if day_diff == 0:
        return f"at {time_str}"
    if day_diff <= 6:
        return f"on {target_dt.strftime('%A')} at {time_str}"
    return f"on {target_dt.strftime('%b %d')} at {time_str}"


# ---------------------------------------------------------------------------
# Atomic Persistence
# ---------------------------------------------------------------------------

def load_reminders() -> List[dict]:
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def save_reminders(reminders: List[dict]) -> None:
    os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
    tmp_path = f"{REMINDERS_FILE}.tmp"
    for _ in range(3):
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(reminders, f, indent=2)
            os.replace(tmp_path, REMINDERS_FILE)
            return
        except Exception:
            time.sleep(0.05)


_last_triggered_reminder: Optional[dict] = None


# ---------------------------------------------------------------------------
# Scheduler Loop & Lifecycle
# ---------------------------------------------------------------------------

def _run_scheduler_loop() -> None:
    global _scheduled_reminders, _last_triggered_reminder
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
                _last_triggered_reminder = dict(r)
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
        valid = []
        for r in stored:
            try:
                if datetime.datetime.fromisoformat(r["target_time"]) > now:
                    valid.append(r)
            except Exception:
                continue
        _scheduled_reminders = valid
        if len(_scheduled_reminders) != len(stored):
            save_reminders(_scheduled_reminders)
    if not _scheduler_running:
        _scheduler_running = True
        threading.Thread(target=_run_scheduler_loop, daemon=True, name="amigo-reminder-scheduler").start()


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------

def handle_set_timer(params: dict, query: str = "") -> str:
    """Starts an interruptible countdown timer from structured parameters."""
    params = params if isinstance(params, dict) else {}
    label = str(params.get("label") or "").strip()

    dur: Optional[float] = None
    if params.get("seconds") is not None:
        try:
            sec_val = float(params["seconds"])
            if sec_val > 0:
                dur = sec_val
        except (ValueError, TypeError):
            pass

    if dur is None:
        dur = (
            parse_relative_seconds(str(params.get("duration") or ""))
            or parse_relative_seconds(query)
            or parse_relative_seconds(label)
        )

    if not dur or dur <= 0:
        return "How long should I set the timer for?"

    timer_id = str(uuid.uuid4())[:8]
    dur_str = _format_duration(dur)
    cancel_ev = threading.Event()
    clean_label = label if (label and parse_relative_seconds(label) is None) else ""

    def _worker():
        cancelled = cancel_ev.wait(timeout=dur)
        with _reminders_lock:
            _active_timers.pop(timer_id, None)
            _timer_cancel_events.pop(timer_id, None)
        if cancelled:
            return
        alert = f"Your {dur_str}{f' {clean_label}' if clean_label else ''} timer is up!"
        print(f"\n[TIMER ALERT] {alert}", flush=True)
        show_desktop_notification("Amigo Timer", alert)
        try:
            from amigo.services.screen_pen import emit_roamies_medal
            emit_roamies_medal(title="Timer Complete", text=f"{dur_str}{f' {clean_label}' if clean_label else ''} completed!")
        except Exception:
            pass
        if _broadcast_callback:
            _broadcast_callback("timer_triggered", {"id": timer_id, "duration": dur, "label": clean_label})
        if _speak_callback:
            _speak_callback(alert)

    with _reminders_lock:
        _timer_cancel_events[timer_id] = cancel_ev
        _active_timers[timer_id] = {
            "id": timer_id,
            "label": clean_label,
            "duration_sec": dur,
            "end_time": (datetime.datetime.now() + datetime.timedelta(seconds=dur)).isoformat(),
        }

    threading.Thread(target=_worker, daemon=True, name=f"amigo-timer-{timer_id}").start()
    label_suffix = f" for {clean_label}" if clean_label else ""
    return f"Timer set for {dur_str}{label_suffix}."


def handle_set_reminder(params: dict, query: str = "") -> str:
    """Schedules or reschedules a persistent reminder for a future time."""
    params = params if isinstance(params, dict) else {}
    time_raw = str(params.get("time") or "").strip()
    msg = str(params.get("message") or "").strip()

    if not time_raw:
        return "When would you like me to remind you?"
    if not msg:
        return "What would you like me to remind you about?"

    target_dt = parse_target_datetime(time_raw)
    if not target_dt:
        return "When would you like me to remind you?"

    rem_id = str(uuid.uuid4())[:8]
    item = {"id": rem_id, "message": msg, "target_time": target_dt.isoformat()}

    with _reminders_lock:
        norm_msg = msg.lower()
        for existing in _scheduled_reminders:
            try:
                e_time = datetime.datetime.fromisoformat(existing["target_time"])
                e_msg = str(existing.get("message", "")).strip().lower()
                is_same_msg = (norm_msg == e_msg) or (
                    len(norm_msg) > 5 and difflib.SequenceMatcher(None, norm_msg, e_msg).ratio() >= 0.88
                )
                if is_same_msg and abs((e_time - target_dt).total_seconds()) <= 120:
                    return f"That reminder is already scheduled {_format_target_when(e_time)}."
            except Exception:
                pass

        _scheduled_reminders.append(item)
        save_reminders(_scheduled_reminders)

    return f"I will remind you to {msg} {_format_target_when(target_dt)}."


def handle_snooze_reminder(params: dict | None = None, duration_seconds: float = 600.0) -> str:
    """Snooze a triggered or active reminder for a given duration (default 10 minutes)."""
    global _last_triggered_reminder, _scheduled_reminders
    params = params if isinstance(params, dict) else {}

    # Extract requested duration from parameters
    dur_secs = 0.0
    if "duration_seconds" in params:
        try:
            dur_secs = float(params["duration_seconds"])
        except (ValueError, TypeError):
            dur_secs = 0.0
    if dur_secs <= 0.0 and "duration" in params:
        parsed = parse_relative_seconds(str(params["duration"]))
        if parsed:
            dur_secs = parsed
    if dur_secs <= 0.0:
        dur_secs = max(10.0, float(duration_seconds))

    reminder_id = str(params.get("reminder_id") or "").strip()

    with _reminders_lock:
        target: Optional[dict] = None
        if reminder_id:
            for r in _scheduled_reminders:
                if str(r.get("id")) == reminder_id:
                    target = r
                    break
        if not target and _last_triggered_reminder:
            target = dict(_last_triggered_reminder)
        elif not target and _scheduled_reminders:
            target = dict(_scheduled_reminders[0])
        elif not target:
            stored = load_reminders()
            if stored:
                target = dict(stored[-1])

        target_msg = target.get("message", "Reminder") if target else "Reminder"
        target_dt = datetime.datetime.now() + datetime.timedelta(seconds=dur_secs)
        new_id = target.get("id") if target else str(uuid.uuid4())[:8]

        item = {
            "id": new_id,
            "message": target_msg,
            "target_time": target_dt.isoformat(),
            "created_at": datetime.datetime.now().isoformat(),
        }

        # Deduplicate and append
        _scheduled_reminders = [r for r in _scheduled_reminders if str(r.get("id")) != str(new_id)]
        _scheduled_reminders.append(item)
        save_reminders(_scheduled_reminders)
        _last_triggered_reminder = None

    dur_str = _format_duration(dur_secs)
    res = f"Snoozed '{target_msg}' for {dur_str}."
    show_desktop_notification("Reminder Snoozed", res)
    return res


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
        lbl = f" '{t['label']}'" if t.get("label") else ""
        items.append(f"Timer{lbl} ({_format_duration(rem)} remaining)")
    for r in reminders:
        dt = datetime.datetime.fromisoformat(r["target_time"])
        items.append(f"Reminder to {r['message']} {_format_target_when(dt)}")

    return "Here are your active schedules: " + ", ".join(items) + "."


def _best_match_index(items: List[dict], query: str, text_key: str) -> int:
    q_low = query.strip().lower()
    if not q_low or not items:
        return -1
    best_idx = -1
    best_score = 0.0
    for idx, item in enumerate(items):
        cand = str(item.get(text_key) or "").strip().lower()
        if not cand:
            continue
        if q_low == cand or q_low in cand or cand in q_low:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, q_low, cand).ratio()
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx if best_score >= 0.55 else -1


def handle_cancel_reminder(params: dict | None = None) -> str:
    """
    Safely cancels a specific reminder/timer by ID or fuzzy query match.
    Only clears all schedules when `all=True` is explicitly provided (never wipes on accidental trigger).
    """
    params = params if isinstance(params, dict) else {}
    target_id = str(params.get("id") or params.get("reminder_id") or "").strip()
    query_topic = str(params.get("query") or params.get("message") or "").strip()
    cancel_all = bool(params.get("all") is True or query_topic.lower() == "all")

    with _reminders_lock:
        if not _active_timers and not _scheduled_reminders:
            return "You have no active timers or reminders."

        # 1. Cancel by explicit ID
        if target_id:
            if target_id in _active_timers:
                _active_timers.pop(target_id, None)
                if ev := _timer_cancel_events.pop(target_id, None):
                    ev.set()
                return "Timer cancelled."
            before = len(_scheduled_reminders)
            _scheduled_reminders[:] = [r for r in _scheduled_reminders if str(r.get("id")) != target_id]
            if len(_scheduled_reminders) < before:
                save_reminders(_scheduled_reminders)
                return "Reminder cancelled."
            return "I couldn't find a reminder with that ID."

        # 2. Explicit cancel-all
        if cancel_all:
            count = len(_active_timers) + len(_scheduled_reminders)
            for ev in _timer_cancel_events.values():
                ev.set()
            _timer_cancel_events.clear()
            _active_timers.clear()
            _scheduled_reminders.clear()
            save_reminders([])
            return f"Cancelled {count} active timer(s) and reminder(s)."

        # 3. Cancel specific reminder or timer by fuzzy topic match
        if query_topic:
            rem_idx = _best_match_index(_scheduled_reminders, query_topic, "message")
            if rem_idx >= 0:
                removed = _scheduled_reminders.pop(rem_idx)
                save_reminders(_scheduled_reminders)
                return f"Cancelled reminder to {removed.get('message', '')}."

            timer_list = list(_active_timers.values())
            t_idx = _best_match_index(timer_list, query_topic, "label")
            if t_idx >= 0:
                tid = timer_list[t_idx]["id"]
                _active_timers.pop(tid, None)
                if ev := _timer_cancel_events.pop(tid, None):
                    ev.set()
                return "Timer cancelled."

            return f"I couldn't find an active reminder or timer matching '{query_topic}'."

        # 4. Safe fallback if neither id, query, nor all=True was given (prevents accidental wipe)
        if len(_scheduled_reminders) == 1 and not _active_timers:
            only = _scheduled_reminders[0]
            return f"Did you want to cancel your reminder to {only.get('message', '')}?"
        return "Which reminder or timer would you like to cancel?"


def get_active_data() -> dict:
    with _reminders_lock:
        return {"timers": list(_active_timers.values()), "reminders": list(_scheduled_reminders)}


def toggle_reminder_done(reminder_id: str) -> bool:
    """Toggles the completed state of a reminder when checked in the Roamies overlay."""
    with _reminders_lock:
        stored = load_reminders()
        found = False
        target_done = False
        for rem in stored:
            if str(rem.get("id")) == str(reminder_id):
                rem["done"] = not rem.get("done", False)
                target_done = rem["done"]
                found = True
                break
        if not found:
            for rem in _scheduled_reminders:
                if str(rem.get("id")) == str(reminder_id):
                    rem["done"] = not rem.get("done", False)
                    target_done = rem["done"]
                    found = True
                    break
        if found:
            save_reminders(stored if stored else _scheduled_reminders)
            for rem in _scheduled_reminders:
                if str(rem.get("id")) == str(reminder_id):
                    rem["done"] = target_done
                    break
            return True
        return False
