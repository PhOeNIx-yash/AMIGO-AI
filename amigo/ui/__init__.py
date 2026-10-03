"""
Amigo UI - User Interface Components
"""

from amigo.ui.server import app, launch_server, broadcaster, set_assistant_state
from amigo.ui.hotkey_service import (
    start_hotkey_service,
    stop_hotkey_service,
    is_hotkey_service_running,
    set_broadcast_callback,
)

__all__ = [
    "app",
    "launch_server",
    "broadcaster",
    "set_assistant_state",
    "start_hotkey_service",
    "stop_hotkey_service",
    "is_hotkey_service_running",
    "set_broadcast_callback",
]