"""
Amigo Memvid Memory Engine (Unified Adapter)
═════════════════════════════════════════════════════════════════════
Delegates to amigo.core.rag_engine to share a single Memvid capsule
instance, lock, and search index across all modules without lock contention.
"""

from typing import Any

from amigo.core import rag_engine

MEMVID_PATH = rag_engine.MEMVID_PATH
CONVERSATIONS = rag_engine.CONVERSATIONS
USER_FACTS = rag_engine.USER_FACTS
DOCUMENTS = rag_engine.DOCUMENTS
EMAILS = rag_engine.EMAILS
CALENDAR = rag_engine.CALENDAR
ALL_COLLECTIONS = rag_engine.ALL_COLLECTIONS

init_memvid = rag_engine.init_memvid
is_memvid_ready = rag_engine.is_memvid_ready
search = rag_engine.search
commit = rag_engine.commit
add_conversation = rag_engine.add_conversation
add_user_fact = rag_engine.add_user_fact
add_document_chunk = rag_engine.add_document_chunk
get_all_user_facts = rag_engine.get_all_user_facts
clear_conversations = rag_engine.clear_conversations
clear_user_facts = rag_engine.clear_user_facts
clear_all_memory = rag_engine.clear_all_memory
get_stats = rag_engine.get_index_stats
