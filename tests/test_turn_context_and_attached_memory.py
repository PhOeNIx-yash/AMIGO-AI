
from amigo.core import llm_agent


def test_build_turn_runtime_context_cache_key_hashes_context():
    # Clear cache before test
    with llm_agent._turn_context_lock:
        llm_agent._turn_context_cache = None

    ctx1 = llm_agent.build_turn_runtime_context(
        query="test query 1",
        doc_context="Document A: Alpha content",
    )
    assert "Document A: Alpha content" in ctx1

    # Second call within 30s with DIFFERENT doc_context should NOT return cached ctx1
    ctx2 = llm_agent.build_turn_runtime_context(
        query="test query 2",
        doc_context="Document B: Beta content",
    )
    assert "Document B: Beta content" in ctx2
    assert "Document A: Alpha content" not in ctx2

    # Second call with DIFFERENT web_context should NOT return cached ctx1 or ctx2
    ctx3 = llm_agent.build_turn_runtime_context(
        query="test query 3",
        web_context="Web Search: Gamma results",
    )
    assert "Web Search: Gamma results" in ctx3
    assert "Document B: Beta content" not in ctx3

    # Call with identical doc_context within 30s SHOULD hit the cache
    ctx2_repeat = llm_agent.build_turn_runtime_context(
        query="test query 2 repeat",
        doc_context="Document B: Beta content",
    )
    assert ctx2_repeat == ctx2


def test_build_ai_messages_suppresses_memory_for_attached_context():
    dummy_history = [
        {"user": "What is on my screen?", "assistant": "A screenshot of an old project."},
        {"user": "What about now?", "assistant": "A web browser showing search results."},
    ]

    # Normal turn with memory enabled
    normal_msgs = llm_agent._build_ai_messages(
        query="Explain this concept",
        use_memory=True,
        doc_context="Simple retrieved snippet",
        conversation_history=dummy_history,
        is_voice=False,
    )
    # History turns should be present
    history_users = [m["content"] for m in normal_msgs if m["role"] == "user"]
    assert any("What is on my screen?" in u for u in history_users)

    # Attached image turn (doc_context starts with '[Attached ')
    attached_msgs = llm_agent._build_ai_messages(
        query="What is in this image?",
        use_memory=True,  # Caller passes True, but it should be overridden to False
        doc_context="[Attached Image: receipt.png]\nExtracted Visual OCR Text:\nTotal: $45.00",
        conversation_history=dummy_history,
        is_voice=False,
    )
    # Prior history turns must NOT be present
    attached_user_contents = [m["content"] for m in attached_msgs if m["role"] == "user"]
    for u_content in attached_user_contents:
        assert "What is on my screen?" not in u_content
        assert "What about now?" not in u_content

    # Only 2 messages: system prompt and current user prompt with attached context
    assert len(attached_msgs) == 2
    assert attached_msgs[0]["role"] == "system"
    assert attached_msgs[1]["role"] == "user"
    assert "[Attached Image: receipt.png]" in attached_msgs[1]["content"]
