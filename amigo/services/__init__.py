"""
Amigo Services - External Integrations
"""

from amigo.services.app_opener import (
    find_files,
    open_folder,
    open_windows_app,
    execute_file_action,
    ensure_built,
    refresh_app_index,
)
from amigo.services.calendar_integration import (
    get_todays_events,
    get_upcoming_events,
    is_outlook_available as calendar_available,
)
from amigo.services.mail_integration import (
    get_recent_emails,
    get_unread_emails,
    search_emails,
    is_outlook_available as mail_available,
)
from amigo.services.os_automation import (
    play_pause_media,
    next_track,
    prev_track,
    set_volume,
    get_system_status,
)
from amigo.services.screen_vision import (
    read_text_from_image,
    analyze_image,
    capture_screen_image,
)
from amigo.services.web_search import search_web
from amigo.services.weather import get_weather_data

__all__ = [
    # App Opener
    "find_files",
    "open_folder",
    "open_windows_app",
    "execute_file_action",
    "ensure_built",
    "refresh_app_index",
    # Calendar
    "get_todays_events",
    "get_upcoming_events",
    "calendar_available",
    # Mail
    "get_recent_emails",
    "get_unread_emails",
    "search_emails",
    "mail_available",
    # OS Automation
    "play_pause_media",
    "next_track",
    "prev_track",
    "set_volume",
    "get_system_status",
    # Screen Vision
    "read_text_from_image",
    "analyze_image",
    "capture_screen_image",
    # Web Search
    "search_web",
    # Weather
    "get_weather_data",
]