"""
Proactive Intelligence Module for Amigo Voice Assistant.
Provides context-aware background assistance, ambient perception,
LLM-synthesized natural suggestions, and non-intrusive wellness check-ins.
"""

import os
import json
import time
import random
import threading
import logging
import datetime
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field, fields
from collections import defaultdict
import psutil

logger = logging.getLogger("amigo.proactive")

# Configuration and state paths
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROACTIVE_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "proactive_config.json")
if not os.path.exists(PROACTIVE_CONFIG_FILE) and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_config.json")):
    PROACTIVE_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_config.json")

PROACTIVE_STATE_FILE = os.path.join(_PROJECT_ROOT, "proactive_state.json")
if not os.path.exists(PROACTIVE_STATE_FILE) and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_state.json")):
    PROACTIVE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_state.json")

# Default configuration tuned for pleasant, non-intrusive assistance
DEFAULT_CONFIG = {
    "enabled": True,
    "monitoring_interval_seconds": 60,
    "battery_low_threshold": 15,
    "battery_critical_threshold": 8,
    "cpu_high_threshold": 92,
    "ram_high_threshold": 95,
    "notify_system_resources": False,       # Disabled by default: prevents spammy CPU/RAM alarms
    "check_weather_interval_minutes": 30,
    "check_calendar_interval_minutes": 15,
    "pattern_learning_enabled": True,
    "max_suggestions_per_hour": 2,          # Max 2 natural suggestions per hour
    "quiet_hours_start": 23,                 # 11 PM
    "quiet_hours_end": 7,                    # 7 AM
    "notification_cooldown_minutes": 45,     # 45-min cooldown between casual suggestions
    "priority_cooldown_minutes": 15,         # 15-min cooldown for critical alerts
    "focus_break_threshold_minutes": 75,    # 75 mins continuous work triggers gentle wellness check
    "voice_enabled": True,
    "use_llm_synthesis": True,              # Synthesize natural suggestions via local LLM
}


@dataclass
class ProactiveState:
    """Runtime state for proactive intelligence."""
    last_weather_check: float = 0
    last_calendar_check: float = 0
    last_system_check: float = 0
    last_notification_time: float = 0
    last_priority_alert_time: float = 0
    suggestions_this_hour: int = 0
    last_hour_reset: float = field(default_factory=time.time)
    active_window_start: float = field(default_factory=time.time)
    current_window_title: str = ""
    recent_suggestions: List[Dict] = field(default_factory=list)
    learned_patterns: Dict = field(default_factory=dict)
    user_routines: Dict = field(default_factory=dict)
    dismissed_suggestions: List = field(default_factory=list)
    acknowledged_suggestions: List = field(default_factory=list)


class ProactiveIntelligence:
    """Main proactive intelligence engine."""

    def __init__(self):
        self.config = self._load_config()
        self.state = self._load_state()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._speak_callback: Optional[Callable[[str], None]] = None
        self._broadcast_callback: Optional[Callable[[str, dict], None]] = None
        self._lock = threading.Lock()
        self._alerted_calendar_events = set()

    def _load_config(self) -> Dict:
        """Load configuration from file."""
        if os.path.exists(PROACTIVE_CONFIG_FILE):
            try:
                with open(PROACTIVE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    config = DEFAULT_CONFIG.copy()
                    config.update(loaded)
                    return config
            except Exception as e:
                logger.warning(f"Failed to load proactive config: {e}")
        return DEFAULT_CONFIG.copy()

    def _save_config(self) -> None:
        """Save configuration to file."""
        try:
            with open(PROACTIVE_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save proactive config: {e}")

    def _load_state(self) -> ProactiveState:
        """Load runtime state from file with robust schema handling."""
        if os.path.exists(PROACTIVE_STATE_FILE):
            try:
                with open(PROACTIVE_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        valid_keys = {f.name for f in fields(ProactiveState)}
                        filtered = {k: v for k, v in data.items() if k in valid_keys}
                        return ProactiveState(**filtered)
            except Exception as e:
                logger.warning(f"Failed to load proactive state: {e}")
        return ProactiveState()

    def _save_state(self) -> None:
        """Save runtime state to file with batched writes."""
        try:
            state_data = self.state.__dict__
            state_hash = hash(str(sorted(state_data.items())))
            if hasattr(self, "_last_state_hash") and self._last_state_hash == state_hash:
                return
            self._last_state_hash = state_hash

            with open(PROACTIVE_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state.__dict__, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save proactive state: {e}")

    def set_callbacks(
        self,
        speak_callback: Optional[Callable[[str], None]] = None,
        broadcast_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        """Set callbacks for speaking and broadcasting."""
        self._speak_callback = speak_callback
        self._broadcast_callback = broadcast_callback

    def start(self) -> None:
        """Start the proactive intelligence background thread."""
        if self._running:
            return
        if not self.config.get("enabled", True):
            logger.info("Proactive intelligence disabled in config")
            return

        self._running = True
        now = time.time()
        # Initialize check timestamps so alerts don't burst on startup
        if not self.state.last_weather_check:
            self.state.last_weather_check = now
        if not self.state.last_calendar_check:
            self.state.last_calendar_check = now
        if not self.state.last_system_check:
            self.state.last_system_check = now
        if not self.state.last_notification_time:
            self.state.last_notification_time = now

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ProactiveIntelligence"
        )
        self._thread.start()
        logger.info("Proactive intelligence started")

    def stop(self) -> None:
        """Stop the proactive intelligence background thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._save_state()
        logger.info("Proactive intelligence stopped")

    def _run_loop(self) -> None:
        """Main background monitoring loop."""
        while self._running:
            try:
                self._check_and_act()
            except Exception as e:
                logger.error(f"Proactive loop error: {e}")

            interval = self.config.get("monitoring_interval_seconds", 60)
            time.sleep(interval)

    def _check_and_act(self) -> None:
        """Check all monitoring conditions and generate suggestions adaptively."""
        now = time.time()
        current_hour = datetime.datetime.now().hour

        # Reset hourly suggestion counter
        if now - self.state.last_hour_reset > 3600:
            self.state.suggestions_this_hour = 0
            self.state.last_hour_reset = now

        # Prune older entries in recent suggestions (keep within 24h)
        if self.state.recent_suggestions:
            cutoff = now - 86400
            self.state.recent_suggestions = [
                s for s in self.state.recent_suggestions if s.get("time", 0) > cutoff
            ][-20:]

        # Fast path for quiet hours (only critical battery bypasses)
        in_quiet = self._is_quiet_hours(current_hour)

        # Check system health (critical battery works even in quiet hours)
        self._check_system_health(now)

        if in_quiet:
            return

        # Check calendar (if enabled and interval passed)
        cal_interval = self.config.get("check_calendar_interval_minutes", 15) * 60
        if now - self.state.last_calendar_check > cal_interval:
            self._check_calendar(now)

        # Check weather (if enabled and interval passed)
        weather_interval = self.config.get("check_weather_interval_minutes", 30) * 60
        if now - self.state.last_weather_check > weather_interval:
            self._check_weather(now)

        # Generate proactive suggestions dynamically
        if self.config.get("pattern_learning_enabled", True):
            self._generate_suggestions(now)

        self._save_state()

    def _is_quiet_hours(self, hour: int) -> bool:
        """Check if current hour is in quiet hours."""
        start = self.config.get("quiet_hours_start", 23)
        end = self.config.get("quiet_hours_end", 7)
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end

    def _check_system_health(self, now: float) -> None:
        """Check system health and notify only on genuine, actionable events."""
        if now - self.state.last_system_check < 45:
            return
        self.state.last_system_check = now

        try:
            battery = psutil.sensors_battery()
            priority_cooldown = self.config.get("priority_cooldown_minutes", 15) * 60

            # Battery checks (actionable and polite)
            if battery and not battery.power_plugged:
                crit_thresh = self.config.get("battery_critical_threshold", 8)
                low_thresh = self.config.get("battery_low_threshold", 15)

                if battery.percent <= crit_thresh:
                    if now - self.state.last_priority_alert_time >= priority_cooldown:
                        self.state.last_priority_alert_time = now
                        self._send_notification(
                            "battery_critical",
                            f"Battery is down to {battery.percent}% and unplugged. Please plug in your charger.",
                            level="critical",
                            is_priority=True,
                        )
                elif battery.percent <= low_thresh:
                    if now - self.state.last_priority_alert_time >= priority_cooldown * 2:
                        self.state.last_priority_alert_time = now
                        self._send_notification(
                            "battery_low",
                            f"Just a quick heads-up: battery is at {battery.percent}%. Consider charging soon.",
                            level="warning",
                            is_priority=True,
                        )

            # System resource checks: ONLY if explicitly enabled to prevent annoying alarms
            if self.config.get("notify_system_resources", False):
                cpu = psutil.cpu_percent(interval=0.1)
                ram = psutil.virtual_memory()
                if cpu > self.config.get("cpu_high_threshold", 92):
                    self._send_notification(
                        "system_cpu",
                        f"System CPU is running high at {cpu:.0f}%.",
                        level="warning",
                    )
                elif ram.percent > self.config.get("ram_high_threshold", 95):
                    self._send_notification(
                        "system_ram",
                        f"Memory usage is at {ram.percent:.0f}%. Consider closing unused apps.",
                        level="warning",
                    )

        except Exception as e:
            logger.debug(f"System health check error: {e}")

    def _check_weather(self, now: float) -> None:
        """Check weather and alert on significant severe conditions."""
        self.state.last_weather_check = now
        try:
            from amigo.services.weather import get_weather_data
            weather = get_weather_data()
            if weather and weather.get("success"):
                condition = weather.get("condition", "").lower()
                temp = weather.get("temp_c") or weather.get("temperature", 0)

                extreme_conditions = ["tornado", "hurricane", "blizzard", "cyclone", "typhoon", "severe storm"]
                for extreme in extreme_conditions:
                    if extreme in condition:
                        self._send_notification(
                            "weather_severe",
                            f"Severe weather advisory: {weather.get('condition', 'Unknown')} at {temp}°C.",
                            level="warning",
                            is_priority=True,
                        )
                        break
        except Exception as e:
            logger.debug(f"Weather check error: {e}")

    def _check_calendar(self, now: float) -> None:
        """Check calendar for upcoming events within the next 15 minutes."""
        self.state.last_calendar_check = now
        try:
            from amigo.services.calendar_integration import get_upcoming_events, is_outlook_available
            if is_outlook_available():
                events = get_upcoming_events(days=1)
                current_time = datetime.datetime.now()

                for event in events:
                    subject = event.get("subject", "Event")
                    start_str = event.get("start", "")
                    event_id = f"{subject}_{start_str}"

                    if event_id in self._alerted_calendar_events:
                        continue

                    if start_str:
                        try:
                            start_time = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            diff = (start_time - current_time).total_seconds() / 60
                            if 0 < diff <= 15:
                                self._alerted_calendar_events.add(event_id)
                                self._send_notification(
                                    "calendar_soon",
                                    f"Upcoming in {int(diff)} minutes: {subject}.",
                                    level="info",
                                    is_priority=True,
                                )
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Calendar check error: {e}")

    def _gather_context(self) -> Dict:
        """Gather rich user, window, activity, and temporal context."""
        now = time.time()
        user_name = "there"
        try:
            from amigo.core.rag_engine import load_profile
            profile = load_profile()
            name = profile.get("identity", {}).get("name", "").strip()
            if name:
                user_name = name
        except Exception:
            pass

        # Foreground window & focus tracking
        window_title = ""
        try:
            from amigo.services.screen_vision import get_active_window_title
            window_title = get_active_window_title().strip()
        except Exception:
            pass

        # Calculate continuous focus duration in current activity
        if window_title and window_title == self.state.current_window_title:
            focus_minutes = max(0, int((now - self.state.active_window_start) / 60))
        else:
            self.state.current_window_title = window_title
            self.state.active_window_start = now
            focus_minutes = 0

        # Activity categorization
        w_lower = window_title.lower()
        if any(k in w_lower for k in ["visual studio", "code", "pycharm", "cursor", "sublime", "terminal", "powershell", "bash", "cmd", "git"]):
            category = "coding"
            activity_label = "coding and development"
        elif any(k in w_lower for k in ["spotify", "youtube", "vlc", "netflix", "music", "video"]):
            category = "media"
            activity_label = "listening to music or streaming media"
        elif any(k in w_lower for k in ["chrome", "edge", "firefox", "brave", "safari", "opera"]):
            category = "browsing"
            activity_label = "browsing the web"
        elif any(k in w_lower for k in ["word", "excel", "powerpoint", "notion", "obsidian", "pdf", "reader", "acrobat"]):
            category = "document"
            activity_label = "reading or writing documents"
        elif any(k in w_lower for k in ["steam", "game", "discord"]):
            category = "gaming_chat"
            activity_label = "gaming or chatting"
        else:
            category = "general"
            activity_label = "working at the computer"

        dt = datetime.datetime.now()
        hour = dt.hour
        day_name = dt.strftime("%A")
        if 5 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 14:
            period = "midday"
        elif 14 <= hour < 18:
            period = "afternoon"
        elif 18 <= hour < 22:
            period = "evening"
        else:
            period = "late night"

        # Ambient media state
        current_media = None
        try:
            from amigo.core.rag_engine import get_active_state
            st = get_active_state(clean_expired=True)
            current_media = st.get("current_media")
        except Exception:
            pass

        return {
            "user_name": user_name,
            "window_title": window_title,
            "category": category,
            "activity_label": activity_label,
            "focus_minutes": focus_minutes,
            "hour": hour,
            "day_name": day_name,
            "period": period,
            "current_media": current_media,
            "timestamp": now,
        }

    def _evaluate_opportunity(self, ctx: Dict) -> Optional[tuple[str, str]]:
        """
        Evaluates context to identify a genuine, timely opportunity for proactive assistance.
        Returns (opportunity_key, description) or None if user should be left undisturbed.
        """
        hour = ctx["hour"]
        period = ctx["period"]
        focus_mins = ctx["focus_minutes"]
        category = ctx["category"]
        break_threshold = self.config.get("focus_break_threshold_minutes", 75)

        # 1. Focus & Wellness: user has been working continuously for 75+ minutes
        if focus_mins >= break_threshold:
            return ("wellness_break", f"User has been focused on {category} for {focus_mins} minutes without a break.")

        # 2. Late Night Wind-Down: active past 11 PM
        if period == "late night" and (hour >= 23 or hour < 5):
            return ("late_night_winddown", f"User is active late at night ({hour:02d}:00).")

        # 3. Morning Kickoff: first activity in the morning
        if period == "morning" and 6 <= hour < 10 and self.state.suggestions_this_hour == 0:
            return ("morning_kickoff", "Start of the morning work session.")

        # 4. Midday Pause: lunchtime during long continuous work
        if period == "midday" and 12 <= hour < 14 and focus_mins >= 40:
            return ("midday_pause", "Midday lunchtime pause during continuous work.")

        # 5. Evening Wrap-up: 5 PM to 7 PM
        if 17 <= hour < 19 and focus_mins >= 35:
            return ("evening_wrapup", "End of day approaching.")

        # 6. Deep coding focus support: coding for 45+ minutes
        if category == "coding" and focus_mins >= 45:
            return ("coding_focus", f"User is deep into coding in {ctx['window_title']}.")

        # 7. Learned routine patterns
        if self.config.get("pattern_learning_enabled", True):
            routine_key = f"{datetime.datetime.now().weekday()}_{hour}"
            if routine_key in self.state.user_routines:
                routine = self.state.user_routines[routine_key]
                if routine.get("count", 0) >= 3:
                    action = routine.get("common_action")
                    if action and action not in self.state.dismissed_suggestions:
                        return (f"routine_{action}", f"User frequently performs action '{action}' around this time.")

        return None

    def _synthesize_suggestion_with_llm(self, ctx: Dict, opp_key: str, opp_desc: str) -> Optional[str]:
        """
        Uses local LLM to generate a natural, context-aware 1-sentence thought.
        Returns None if LLM determines user should not be disturbed (PASS) or is busy.
        """
        try:
            from amigo.core.llm_agent import query_local_llm, _inference_lock
            # If user is currently conversing with Amigo, do not interrupt
            if _inference_lock.locked():
                return None

            prompt = (
                f"Context:\n"
                f"- User: {ctx['user_name']}\n"
                f"- Time: {ctx['day_name']} {ctx['period']} ({ctx['hour']:02d}:00)\n"
                f"- Active App: {ctx['window_title'] or 'Desktop'}\n"
                f"- Activity: {ctx['activity_label']} ({ctx['focus_minutes']}m continuous)\n"
                f"- Situation: {opp_desc}\n\n"
                f"Evaluate if an unobtrusive check-in is helpful right now.\n"
                f"If the user is in flow and interrupting would be annoying, reply PASS.\n"
                f"Otherwise, write 1 warm, natural sentence addressing {ctx['user_name']} (max 15 words).\n"
                f"Do not use markdown, quotes, or robotic assistant clichés."
            )

            system_prompt = (
                f"You are Amigo, a perceptive and friendly AI companion to {ctx['user_name']}. "
                f"Speak naturally like a thoughtful human friend. "
                f"Output at most 1 short sentence (under 16 words). "
                f"If no interruption is warranted, output PASS."
            )

            res = query_local_llm(
                prompt,
                system_prompt=system_prompt,
                max_tokens=50,
                temperature=0.75,
                thinking=False,
                sanitize=True,
                stop=["\n", "User:", "Amigo:"],
            )
            if not res:
                return None

            clean = res.strip().strip('"').strip("'").strip()
            # If LLM opted to PASS, respect its decision and stay silent
            if clean.upper() == "PASS" or "PASS" in clean.upper()[:6]:
                return None
            if "having trouble" in clean.lower() or len(clean.split()) > 25 or len(clean) < 6:
                return None

            return clean
        except Exception as e:
            logger.debug(f"[Proactive] LLM synthesis fallback: {e}")
            return None

    def _get_dynamic_fallback(self, ctx: Dict, opp_key: str) -> str:
        """Diverse, natural fallback templates parameterized with user context."""
        name = ctx["user_name"]
        app = ctx["window_title"] or "your work"
        app_short = app.split(" - ")[-1].strip() if " - " in app else app
        if len(app_short) > 20:
            app_short = "your work"

        pools = {
            "wellness_break": [
                f"You've been in the zone for quite a while, {name}. Don't forget to take a quick stretch and hydrate.",
                f"Friendly reminder to rest your eyes for a moment, {name}—you've been working hard.",
                f"Great focus session so far, {name}! Good time to stand up and grab some water when you hit a pause.",
                f"Remember to take a breather whenever you reach a natural stopping point, {name}.",
            ],
            "late_night_winddown": [
                f"Working late tonight, {name}? Remember to get some rest when you reach a good stopping spot.",
                f"Burning the midnight oil, {name}! Let me know if you want me to jot down any quick reminders for tomorrow.",
                f"It's getting pretty late, {name}—don't forget to get some good sleep tonight.",
            ],
            "morning_kickoff": [
                f"Good morning, {name}! Hope your day gets off to a great start. I'm right here if you need anything.",
                f"Morning, {name}! Let me know if you'd like me to pull up your schedule or the weather today.",
                f"Good morning, {name}! Wishing you a smooth and productive day ahead.",
            ],
            "midday_pause": [
                f"Getting close to lunchtime, {name}. Remember to take a breather and grab some food when you're ready.",
                f"Midday already! Don't forget to step away and refuel whenever you're at a good pausing point, {name}.",
            ],
            "evening_wrapup": [
                f"Heading towards the evening, {name}. Let me know if you'd like to review any notes or wrap things up.",
                f"Wrapping up for the day soon, {name}? I'm here if you need any reminders set for tomorrow.",
            ],
            "coding_focus": [
                f"Deep in the code, {name}! Let me know if you'd like some focus music or help documenting anything.",
                f"Great coding flow in {app_short}, {name}. Let me know if you hit any roadblocks.",
            ],
        }

        candidates = pools.get(opp_key)
        if not candidates:
            if opp_key.startswith("routine_"):
                action = opp_key.replace("routine_", "")
                candidates = [
                    f"Noticed you often check {action} around this time, {name}. Would you like me to pull that up?",
                    f"Want a hand with {action}, {name}? You usually check this about now.",
                ]
            else:
                candidates = [
                    f"Hope everything is going smoothly today, {name}. Let me know if you need any assistance.",
                    f"Standing by if there's anything I can help you with, {name}.",
                ]

        # Filter out recently used candidates to avoid repetition
        recent_texts = {r.get("text", "") for r in self.state.recent_suggestions}
        fresh = [c for c in candidates if c not in recent_texts]
        if fresh:
            return random.choice(fresh)
        return random.choice(candidates)

    def _generate_suggestions(self, now: float) -> None:
        """Generate proactive suggestions adaptively based on context, flow, and learning."""
        max_per_hour = self.config.get("max_suggestions_per_hour", 2)
        if self.state.suggestions_this_hour >= max_per_hour:
            return

        cooldown = self.config.get("notification_cooldown_minutes", 45) * 60
        if now - self.state.last_notification_time < cooldown:
            return

        ctx = self._gather_context()
        opp = self._evaluate_opportunity(ctx)
        if not opp:
            return

        opp_key, opp_desc = opp

        # Check if this specific opportunity was dismissed
        if opp_key in self.state.dismissed_suggestions:
            return

        # Attempt natural LLM synthesis if enabled
        message = None
        if self.config.get("use_llm_synthesis", True):
            message = self._synthesize_suggestion_with_llm(ctx, opp_key, opp_desc)

        # If LLM returned PASS or was skipped, message is None
        # If user explicitly wants suggestions or LLM gave null due to lock, use diverse fallback
        if not message and opp_key in ("wellness_break", "late_night_winddown"):
            message = self._get_dynamic_fallback(ctx, opp_key)

        if not message:
            return

        # Check recent duplication
        recent_texts = {r.get("text", "").strip().lower() for r in self.state.recent_suggestions}
        if message.strip().lower() in recent_texts:
            return

        # Record and emit
        self.state.recent_suggestions.append({
            "id": opp_key,
            "text": message,
            "time": now,
        })
        self._send_notification("suggestion", message, level="suggestion", suggestion_id=opp_key)

    def _send_notification(
        self,
        notification_id: str,
        message: str,
        level: str = "info",
        suggestion_id: str = None,
        is_priority: bool = False,
    ) -> None:
        """Send a proactive notification with proper channel dispatching and cooldowns."""
        now = time.time()
        cooldown = self.config.get("notification_cooldown_minutes", 45) * 60

        if not is_priority and (now - self.state.last_notification_time < cooldown):
            return

        # 1. Broadcast to UI
        if self._broadcast_callback:
            self._broadcast_callback(
                "proactive_notification",
                {
                    "id": notification_id,
                    "message": message,
                    "level": level,
                    "suggestion_id": suggestion_id,
                    "timestamp": datetime.datetime.now().isoformat(),
                },
            )
            self._broadcast_callback(
                "chat_message",
                {
                    "sender": "assistant",
                    "text": message,
                    "user_query": f"Proactive ({level.title()})",
                    "tool": "proactive",
                    "level": level,
                },
            )

        # 2. Voice output
        voice_allowed = self.config.get("voice_enabled", True)
        if (voice_allowed or level == "critical") and self._speak_callback:
            try:
                self._speak_callback(message)
            except Exception as e:
                logger.error(f"[Proactive] Error speaking notification: {e}")

        self.state.last_notification_time = now
        if level == "suggestion":
            self.state.suggestions_this_hour += 1

        logger.info(f"Proactive notification [{level}]: {message}")

    def trigger_notification(
        self, message: str = None, level: str = "info", notification_id: str = "manual_trigger"
    ) -> str:
        """Manually trigger a proactive notification, evaluating dynamically if message is omitted."""
        if not message or message in ("generate", "auto", "dynamic"):
            ctx = self._gather_context()
            opp = self._evaluate_opportunity(ctx) or ("wellness_break", "Demonstration proactive check-in.")
            opp_key, opp_desc = opp
            synthesized = None
            if self.config.get("use_llm_synthesis", True):
                synthesized = self._synthesize_suggestion_with_llm(ctx, opp_key, opp_desc)
            message = synthesized or self._get_dynamic_fallback(ctx, opp_key)

        if self._broadcast_callback:
            self._broadcast_callback(
                "proactive_notification",
                {
                    "id": notification_id,
                    "message": message,
                    "level": level,
                    "timestamp": datetime.datetime.now().isoformat(),
                },
            )
            self._broadcast_callback(
                "chat_message",
                {
                    "sender": "assistant",
                    "text": message,
                    "user_query": f"Proactive ({level.title()})",
                    "tool": "proactive",
                    "level": level,
                },
            )

        voice_allowed = self.config.get("voice_enabled", True)
        if (voice_allowed or level == "critical") and self._speak_callback:
            try:
                self._speak_callback(message)
            except Exception as e:
                logger.error(f"[Proactive] Error speaking notification: {e}")

        self.state.last_notification_time = time.time()
        logger.info(f"Proactive manual trigger [{level}]: {message}")
        return message

    def record_user_action(self, action: str, context: Dict = None) -> None:
        """Record user action for habit and pattern learning."""
        if not self.config.get("pattern_learning_enabled", True):
            return

        current_hour = datetime.datetime.now().hour
        current_day = datetime.datetime.now().weekday()
        routine_key = f"{current_day}_{current_hour}"

        with self._lock:
            if routine_key not in self.state.user_routines:
                self.state.user_routines[routine_key] = {"actions": defaultdict(int), "count": 0}

            self.state.user_routines[routine_key]["actions"][action] += 1
            self.state.user_routines[routine_key]["count"] += 1

            actions = self.state.user_routines[routine_key]["actions"]
            if actions:
                self.state.user_routines[routine_key]["common_action"] = max(actions, key=actions.get)

            if len(actions) > 10:
                top_actions = dict(sorted(actions.items(), key=lambda x: x[1], reverse=True)[:10])
                self.state.user_routines[routine_key]["actions"] = top_actions

    def dismiss_suggestion(self, suggestion_id: str) -> None:
        """Dismiss a suggestion category so it won't be shown repeatedly."""
        if suggestion_id not in self.state.dismissed_suggestions:
            self.state.dismissed_suggestions.append(suggestion_id)
            self._save_state()

    def acknowledge_suggestion(self, suggestion_id: str) -> None:
        """Acknowledge a suggestion was helpful."""
        if suggestion_id not in self.state.acknowledged_suggestions:
            self.state.acknowledged_suggestions.append(suggestion_id)
            self._save_state()

    def get_status(self) -> Dict:
        """Get current proactive intelligence status."""
        ctx = self._gather_context()
        return {
            "enabled": self.config.get("enabled", True),
            "running": self._running,
            "config": self.config,
            "context": {
                "user_name": ctx.get("user_name"),
                "activity": ctx.get("activity_label"),
                "focus_minutes": ctx.get("focus_minutes"),
                "period": ctx.get("period"),
            },
            "state": {
                "suggestions_this_hour": self.state.suggestions_this_hour,
                "recent_suggestions_count": len(self.state.recent_suggestions),
                "learned_patterns_count": len(self.state.learned_patterns),
                "user_routines_count": len(self.state.user_routines),
                "dismissed_count": len(self.state.dismissed_suggestions),
            },
        }

    def update_config(self, new_config: Dict) -> None:
        """Update configuration."""
        self.config.update(new_config)
        self._save_config()


# Global instance
_proactive_instance: Optional[ProactiveIntelligence] = None


def get_proactive_intelligence() -> ProactiveIntelligence:
    """Get or create the global proactive intelligence instance."""
    global _proactive_instance
    if _proactive_instance is None:
        _proactive_instance = ProactiveIntelligence()
    return _proactive_instance


def init_proactive_intelligence(
    speak_callback: Callable[[str], None] = None,
    broadcast_callback: Callable[[str, dict], None] = None,
) -> ProactiveIntelligence:
    """Initialize and start proactive intelligence."""
    global _proactive_instance
    _proactive_instance = ProactiveIntelligence()
    _proactive_instance.set_callbacks(speak_callback, broadcast_callback)
    _proactive_instance.start()
    return _proactive_instance


def shutdown_proactive_intelligence() -> None:
    """Shutdown proactive intelligence."""
    global _proactive_instance
    if _proactive_instance:
        _proactive_instance.stop()
        _proactive_instance = None