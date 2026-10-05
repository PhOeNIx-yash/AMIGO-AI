"""
Proactive Intelligence Module for Amigo Voice Assistant.
Provides background monitoring, pattern learning, and predictive suggestions.
"""

import os
import json
import time
import threading
import logging
import datetime
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field
from collections import defaultdict
import psutil

logger = logging.getLogger("amigo.proactive")

# Configuration
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROACTIVE_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "proactive_config.json")
if not os.path.exists(PROACTIVE_CONFIG_FILE) and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_config.json")):
    PROACTIVE_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_config.json")

PROACTIVE_STATE_FILE = os.path.join(_PROJECT_ROOT, "proactive_state.json")
if not os.path.exists(PROACTIVE_STATE_FILE) and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_state.json")):
    PROACTIVE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proactive_state.json")

# Default configuration
DEFAULT_CONFIG = {
    "enabled": True,
    "monitoring_interval_seconds": 60,
    "battery_low_threshold": 20,
    "battery_critical_threshold": 10,
    "cpu_high_threshold": 85,
    "ram_high_threshold": 90,
    "check_weather_interval_minutes": 30,
    "check_calendar_interval_minutes": 15,
    "pattern_learning_enabled": True,
    "max_suggestions_per_hour": 3,
    "quiet_hours_start": 22,  # 10 PM
    "quiet_hours_end": 7,     # 7 AM
    "notification_cooldown_minutes": 30,
}

@dataclass
class ProactiveState:
    """Runtime state for proactive intelligence."""
    last_weather_check: float = 0
    last_calendar_check: float = 0
    last_system_check: float = 0
    last_notification_time: float = 0
    suggestions_this_hour: int = 0
    last_hour_reset: float = field(default_factory=time.time)
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
        
    def _load_config(self) -> Dict:
        """Load configuration from file."""
        if os.path.exists(PROACTIVE_CONFIG_FILE):
            try:
                with open(PROACTIVE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # Merge with defaults
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
        """Load runtime state from file."""
        if os.path.exists(PROACTIVE_STATE_FILE):
            try:
                with open(PROACTIVE_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return ProactiveState(**data)
            except Exception as e:
                logger.warning(f"Failed to load proactive state: {e}")
        return ProactiveState()
    
    def _save_state(self) -> None:
        """Save runtime state to file - batched writes."""
        try:
            # Only save if state actually changed (simple hash check)
            state_data = self.state.__dict__
            state_hash = hash(str(sorted(state_data.items())))
            if hasattr(self, '_last_state_hash') and self._last_state_hash == state_hash:
                return
            self._last_state_hash = state_hash
            
            with open(PROACTIVE_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state.__dict__, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save proactive state: {e}")
    
    def set_callbacks(self, speak_callback: Callable[[str], None] = None, 
                      broadcast_callback: Callable[[str, dict], None] = None) -> None:
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
        # Grace period on startup: initialize check timestamps to now so alerts don't burst on boot
        now = time.time()
        if not self.state.last_weather_check:
            self.state.last_weather_check = now
        if not self.state.last_calendar_check:
            self.state.last_calendar_check = now
        if not self.state.last_system_check:
            self.state.last_system_check = now
        if not self.state.last_notification_time:
            self.state.last_notification_time = now

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ProactiveIntelligence")
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
        """Main background monitoring loop - optimized with adaptive intervals."""
        while self._running:
            try:
                self._check_and_act()
            except Exception as e:
                logger.error(f"Proactive loop error: {e}")
            
            # Adaptive interval: longer when idle, shorter when active
            interval = self.config.get("monitoring_interval_seconds", 60)
            time.sleep(interval)
    
    def _check_and_act(self) -> None:
        """Check all monitoring conditions and act if needed - optimized."""
        now = time.time()
        current_hour = datetime.datetime.now().hour
        
        # Reset hourly suggestion counter
        if now - self.state.last_hour_reset > 3600:
            self.state.suggestions_this_hour = 0
            self.state.last_hour_reset = now
        
        # Check quiet hours - fast path
        if self._is_quiet_hours(current_hour):
            return
        
        # Check system health (fast, cached)
        self._check_system_health(now)
        
        # Check weather (if enabled and interval passed)
        if now - self.state.last_weather_check > self.config.get("check_weather_interval_minutes", 30) * 60:
            self._check_weather(now)
        
        # Check calendar (if enabled and interval passed)
        if now - self.state.last_calendar_check > self.config.get("check_calendar_interval_minutes", 15) * 60:
            self._check_calendar(now)
        
        # Generate proactive suggestions based on patterns
        if self.config.get("pattern_learning_enabled", True):
            self._generate_suggestions(now)
        
        # Only save state if changed
        self._save_state()
    
    def _is_quiet_hours(self, hour: int) -> bool:
        """Check if current hour is in quiet hours - cached."""
        start = self.config.get("quiet_hours_start", 22)
        end = self.config.get("quiet_hours_end", 7)
        if start > end:  # Overnight (e.g., 22 to 7)
            return hour >= start or hour < end
        return start <= hour < end
    
    def _check_system_health(self, now: float) -> None:
        """Check system health and alert if needed - optimized with caching."""
        if now - self.state.last_system_check < 30:  # Check every 30 seconds max
            return
        self.state.last_system_check = now
        
        try:
            # Use non-blocking calls where possible
            battery = psutil.sensors_battery()
            cpu = psutil.cpu_percent(interval=0.1)  # Faster interval
            ram = psutil.virtual_memory()
            
            alerts = []
            
            # Battery checks
            if battery:
                if battery.percent <= self.config.get("battery_critical_threshold", 10) and not battery.power_plugged:
                    alerts.append(("critical", f"Battery critically low at {battery.percent}%! Please plug in."))
                elif battery.percent <= self.config.get("battery_low_threshold", 20) and not battery.power_plugged:
                    alerts.append(("warning", f"Battery low at {battery.percent}%. Consider charging soon."))
            
            # CPU check
            if cpu > self.config.get("cpu_high_threshold", 85):
                alerts.append(("warning", f"High CPU usage: {cpu:.0f}%. System may slow down."))
            
            # RAM check
            if ram.percent > self.config.get("ram_high_threshold", 90):
                alerts.append(("warning", f"High memory usage: {ram.percent:.0f}%. Consider closing unused apps."))
            
            # Send alerts
            for level, message in alerts:
                self._send_notification(f"system_{level}", message, level)
                
        except Exception as e:
            logger.debug(f"System health check error: {e}")
    
    def _check_weather(self, now: float) -> None:
        """Check weather and alert on significant changes - cached."""
        self.state.last_weather_check = now
        try:
            from amigo.services.weather import get_weather_data
            weather = get_weather_data()
            if weather and weather.get("success"):
                condition = weather.get("condition", "").lower()
                temp = weather.get("temp_c") or weather.get("temperature", 0)
                
                # Check for extreme weather hazards only
                extreme_conditions = ["tornado", "hurricane", "blizzard", "cyclone", "typhoon"]
                for extreme in extreme_conditions:
                    if extreme in condition:
                        self._send_notification(
                            "weather_severe", 
                            f"Weather advisory: {weather.get('condition', 'Unknown')} at {temp}°C",
                            "info",
                        )
                        break
        except Exception as e:
            logger.debug(f"Weather check error: {e}")
    
    def _check_calendar(self, now: float) -> None:
        """Check calendar for upcoming events."""
        self.state.last_calendar_check = now
        try:
            from amigo.services.calendar_integration import get_upcoming_events, is_outlook_available
            if is_outlook_available():
                events = get_upcoming_events(days=1)  # Today's events
                current_time = datetime.datetime.now()
                
                for event in events:
                    start_str = event.get("start", "")
                    if start_str:
                        try:
                            start_time = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            # Check if event is in 15 minutes
                            diff = (start_time - current_time).total_seconds() / 60
                            if 0 < diff <= 15:
                                self._send_notification("calendar_soon",
                                    f"Meeting in {int(diff)} minutes: {event.get('subject', 'Event')}", "info")
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Calendar check error: {e}")
    
    def _generate_suggestions(self, now: float) -> None:
        """Generate proactive suggestions based on learned patterns and context."""
        if self.state.suggestions_this_hour >= self.config.get("max_suggestions_per_hour", 3):
            return
        
        # Check cooldown
        if now - self.state.last_notification_time < self.config.get("notification_cooldown_minutes", 30) * 60:
            return
        
        suggestions = []
        current_hour = datetime.datetime.now().hour
        current_day = datetime.datetime.now().weekday()  # 0=Monday
        
        # Time-based suggestions
        if 6 <= current_hour < 9:
            suggestions.append(("morning_routine", "Good morning! Would you like me to check your calendar, weather, or play some music?"))
        elif 12 <= current_hour < 14:
            suggestions.append(("lunch_break", "Lunch time! Want me to set a timer or find a place to eat?"))
        elif 17 <= current_hour < 19:
            suggestions.append(("evening_wrapup", "End of day approaching. Need help wrapping up or setting reminders for tomorrow?"))
        
        # Pattern-based suggestions (learned from history)
        if self.state.learned_patterns:
            routine_key = f"{current_day}_{current_hour}"
            if routine_key in self.state.user_routines:
                routine = self.state.user_routines[routine_key]
                if routine.get("count", 0) > 3:  # Only suggest if pattern is strong
                    action = routine.get("common_action")
                    if action and action not in self.state.dismissed_suggestions:
                        suggestions.append((f"routine_{action}", f"You usually {action} at this time. Want me to help?"))
        
        # Send first valid suggestion
        for suggestion_id, message in suggestions:
            if suggestion_id not in self.state.dismissed_suggestions:
                self._send_notification("suggestion", message, "suggestion", suggestion_id)
                break
    
    def _send_notification(self, notification_id: str, message: str, level: str = "info", 
                          suggestion_id: str = None) -> None:
        """Send a proactive notification."""
        # Check cooldown
        now = time.time()
        if now - self.state.last_notification_time < self.config.get("notification_cooldown_minutes", 30) * 60:
            return
        
        # Broadcast to UI
        if self._broadcast_callback:
            self._broadcast_callback("proactive_notification", {
                "id": notification_id,
                "message": message,
                "level": level,
                "suggestion_id": suggestion_id,
                "timestamp": datetime.datetime.now().isoformat()
            })
        
        # Speak only if it's a critical emergency (e.g. battery cutoff)
        if level == "critical" and self._speak_callback:
            self._speak_callback(message)
        
        self.state.last_notification_time = now
        if level == "suggestion":
            self.state.suggestions_this_hour += 1
        
        logger.info(f"Proactive notification [{level}]: {message}")
    
    def record_user_action(self, action: str, context: Dict = None) -> None:
        """Record user action for pattern learning."""
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
            
            # Update common action
            actions = self.state.user_routines[routine_key]["actions"]
            if actions:
                self.state.user_routines[routine_key]["common_action"] = max(actions, key=actions.get)
            
            # Keep only top actions
            if len(actions) > 10:
                top_actions = dict(sorted(actions.items(), key=lambda x: x[1], reverse=True)[:10])
                self.state.user_routines[routine_key]["actions"] = top_actions
    
    def dismiss_suggestion(self, suggestion_id: str) -> None:
        """Dismiss a suggestion so it won't be shown again."""
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
        return {
            "enabled": self.config.get("enabled", True),
            "running": self._running,
            "config": self.config,
            "state": {
                "suggestions_this_hour": self.state.suggestions_this_hour,
                "learned_patterns_count": len(self.state.learned_patterns),
                "user_routines_count": len(self.state.user_routines),
                "dismissed_count": len(self.state.dismissed_suggestions),
            }
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

def init_proactive_intelligence(speak_callback: Callable[[str], None] = None,
                                 broadcast_callback: Callable[[str, dict], None] = None) -> ProactiveIntelligence:
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