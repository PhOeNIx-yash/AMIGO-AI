"""
Amigo Utils - Utility Functions
"""

from amigo.utils.tts import (
    speak,
    stop_speaking,
    set_tts_callbacks,
    get_tts_engine_name,
    transcribe_b64,
)
from amigo.utils.tool_registry import (
    UI_TOOL_HANDLERS,
    build_action_cards,
    execute_tool,
    set_media_update_callback,
    _tool_chat,
)
from amigo.utils.network_utils import is_internet_connected
from amigo.utils.settings_resolver import resolve_setting as resolve_settings

__all__ = [
    # TTS
    "speak",
    "stop_speaking",
    "set_tts_callbacks",
    "get_tts_engine_name",
    "transcribe_b64",
    # Tool Registry
    "UI_TOOL_HANDLERS",
    "build_action_cards",
    "execute_tool",
    "set_media_update_callback",
    "_tool_chat",
    # Network
    "is_internet_connected",
    # Settings
    "resolve_settings",
]