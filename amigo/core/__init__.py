"""
Amigo Core Modules
"""

from amigo.core.ai import (
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
    set_thinking_enabled,
    is_thinking_enabled,
)

from amigo.core.local_llm import (
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
    set_thinking_enabled as set_llm_thinking_enabled,
    is_thinking_enabled as is_llm_thinking_enabled,
    is_creativity_enabled,
    get_sampling_params,
)

from amigo.core.llm_agent import (
    get_agent_action,
    get_agent_actions,
    initialize_agent,
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

from amigo.core.reminder_timer import (
    init_reminders,
    handle_set_timer,
    handle_set_reminder,
    handle_cancel_reminder,
    get_active_data,
)

from amigo.core.task_agent import (
    get_desktop_context,
    execute_desktop_action,
)

__all__ = [
    # AI
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
    # Reminder Timer
    "init_reminders",
    "handle_set_timer",
    "handle_set_reminder",
    "handle_cancel_reminder",
    "get_active_data",
    # Task Agent
    "get_desktop_context",
    "execute_desktop_action",
]