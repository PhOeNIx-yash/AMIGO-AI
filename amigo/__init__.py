"""
Amigo Voice Assistant - Main Package
"""

from amigo.core.llm_agent import (
    get_ai_response,
    get_ai_response_stream,
    get_quick_feedback,
    add_to_memory,
    load_memory,
    save_memory,
    get_active_state,
    update_active_state,
    clear_conversations_memory,
    extract_user_profile_updates,
    get_user_profile_prompt,
    get_active_context_prompt,
    get_last_thought,
    initialize_agent,
)

from amigo.utils.tool_registry import (
    get_agent_action,
    get_agent_actions,
)

from amigo.core.llm_agent import (
    init_local_llm,
    query_local_llm,
    query_local_llm_stream,
    get_active_model_info,
    get_available_models,
    set_active_model,
    is_vision_ready,
    get_clipboard_text,
    sanitize_for_tts,
    stream_sentence_chunks,
    set_thinking_enabled,
    is_thinking_enabled,
    is_creativity_enabled,
    get_sampling_params,
)

from amigo.core.rag_engine import (
    init_rag,
    is_rag_ready,
    search,
    add_conversation,
    add_user_fact,
    get_recent_conversations,
    get_all_conversations,
    clear_conversations,
    get_index_stats,
    load_memory as load_rag_memory,
    save_memory as save_rag_memory,
    get_active_state as get_rag_active_state,
    update_active_state as update_rag_active_state,
)

from amigo.core.proactive_intelligence import (
    init_proactive_intelligence,
    shutdown_proactive_intelligence,
    get_proactive_intelligence,
)

from amigo.services.app_opener import find_files, open_folder, open_windows_app, execute_file_action
from amigo.services.calendar_integration import get_todays_events, get_upcoming_events, is_outlook_available as calendar_available
from amigo.services.mail_integration import get_recent_emails, get_unread_emails, search_emails, is_outlook_available as mail_available
from amigo.services.os_automation import play_pause_media, next_track, prev_track, set_volume, get_system_status
from amigo.services.screen_vision import read_text_from_image, analyze_image, capture_screen_image
from amigo.services.web_search import search_web
from amigo.services.weather import get_weather_data

from amigo.ui.server import launch_server, app
from amigo.ui.hotkey_service import start_hotkey_service, is_hotkey_service_running

from amigo.utils.tts import speak, stop_speaking, set_tts_callbacks, get_tts_engine_name, transcribe_b64
from amigo.utils.tool_registry import UI_TOOL_HANDLERS, build_action_cards, execute_tool, set_media_update_callback
from amigo.utils.network_utils import is_internet_connected
from amigo.utils import resolve_settings

__version__ = "2.0.0"
__all__ = [
    # Core AI
    "get_ai_response",
    "get_ai_response_stream",
    "get_quick_feedback",
    "add_to_memory",
    "load_memory",
    "save_memory",
    "get_active_state",
    "update_active_state",
    "clear_conversations_memory",
    "extract_user_profile_updates",
    "get_user_profile_prompt",
    "get_active_context_prompt",
    "get_last_thought",
    "set_thinking_enabled",
    "is_thinking_enabled",
    # Local LLM
    "init_local_llm",
    "query_local_llm",
    "query_local_llm_stream",
    "get_active_model_info",
    "get_available_models",
    "set_active_model",
    "is_vision_ready",
    "get_clipboard_text",
    "sanitize_for_tts",
    "stream_sentence_chunks",
    "set_llm_thinking_enabled",
    "is_llm_thinking_enabled",
    "is_creativity_enabled",
    "get_sampling_params",
    # LLM Agent
    "get_agent_action",
    "get_agent_actions",
    "initialize_agent",
    # RAG Engine
    "init_rag",
    "is_rag_ready",
    "search",
    "add_conversation",
    "add_user_fact",
    "get_recent_conversations",
    "get_all_conversations",
    "clear_conversations",
    "get_index_stats",
    "load_rag_memory",
    "save_rag_memory",
    "get_rag_active_state",
    "update_rag_active_state",
    # Proactive Intelligence
    "init_proactive_intelligence",
    "shutdown_proactive_intelligence",
    "get_proactive_intelligence",
    # Services
    "open_app",
    "close_app",
    "find_files",
    "open_folder",
    "open_windows_app",
    "execute_file_action",
    "get_todays_events",
    "get_upcoming_events",
    "calendar_available",
    "get_recent_emails",
    "get_unread_emails",
    "search_emails",
    "mail_available",
    "play_pause_media",
    "next_track",
    "prev_track",
    "set_volume",
    "get_system_status",
    "read_text_from_image",
    "analyze_image",
    "capture_screen_image",
    "search_web",
    "get_weather_data",
    # UI
    "launch_server",
    "app",
    "start_hotkey_service",
    "is_hotkey_service_running",
    # Utils
    "speak",
    "stop_speaking",
    "set_tts_callbacks",
    "get_tts_engine_name",
    "transcribe_b64",
    "UI_TOOL_HANDLERS",
    "build_action_cards",
    "execute_tool",
    "set_media_update_callback",
    "is_internet_connected",
    "resolve_settings",
]