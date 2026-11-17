import time
from unittest.mock import MagicMock, patch

from amigo.core import llm_agent, rag_engine
from amigo.ui import server
from amigo.utils import tool_registry


def test_llm_model_cleanup_on_set_active_model():
    mock_llm = MagicMock()
    with patch.object(llm_agent, "_local_llm_instance", mock_llm), \
         patch.object(llm_agent, "AVAILABLE_MODELS", {"test-model": {"path": "test", "name": "Test"}}), \
         patch.object(llm_agent, "init_local_llm", return_value=mock_llm), \
         patch("gc.collect") as mock_gc:
        ok = llm_agent.set_active_model("test-model")
        assert ok is True
        mock_llm.close.assert_called_once()
        mock_gc.assert_called()


def test_llm_cleanup_on_atexit():
    mock_llm = MagicMock()
    with patch.object(llm_agent, "_local_llm_instance", mock_llm), \
         patch("gc.collect") as mock_gc:
        llm_agent._cleanup_llm_model()
        mock_llm.close.assert_called_once()
        mock_gc.assert_called()
        assert llm_agent._local_llm_instance is None


def test_llm_query_calls_reset_on_error():
    mock_llm = MagicMock()
    mock_llm.create_chat_completion.side_effect = RuntimeError("exceed context window")
    with patch.object(llm_agent, "init_local_llm", return_value=mock_llm):
        llm_agent.query_local_llm("test prompt", max_tokens=64)
        mock_llm.reset.assert_called()


def test_llm_stream_closes_stream_and_resets():
    mock_llm = MagicMock()
    mock_stream = MagicMock()
    mock_stream.__iter__.return_value = [RuntimeError("inference error")]
    mock_llm.create_chat_completion.side_effect = RuntimeError("stream failure")

    with patch.object(llm_agent, "init_local_llm", return_value=mock_llm):
        tokens = list(llm_agent.query_local_llm_stream("test prompt"))
        assert len(tokens) > 0
        mock_llm.reset.assert_called()


def test_rag_frame_cache_ttl_and_max():
    with rag_engine._frame_cache_lock:
        rag_engine._FRAME_CACHE.clear()

    mock_mv = MagicMock()
    mock_mv.frame.return_value = {"text": "frame data", "tags": []}

    # Fetch to populate
    d1 = rag_engine._get_cached_frame(mock_mv, "uri://test1")
    assert d1 == {"text": "frame data", "tags": []}
    assert "uri://test1" in rag_engine._FRAME_CACHE

    # Test TTL eviction: simulate old timestamp
    with rag_engine._frame_cache_lock:
        old_time = time.monotonic() - 200.0
        rag_engine._FRAME_CACHE["uri://test1"] = (old_time, {"text": "stale"})

    mock_mv.frame.return_value = {"text": "fresh data", "tags": []}
    d2 = rag_engine._get_cached_frame(mock_mv, "uri://test1")
    assert d2 == {"text": "fresh data", "tags": []}


def test_rag_extracted_text_cache_ttl_and_size_bounds(tmp_path):
    test_file = tmp_path / "sample.txt"
    test_file.write_text("Hello world content", encoding="utf-8")

    with rag_engine._extracted_text_lock:
        rag_engine._extracted_text_cache.clear()

    # First extraction
    txt = rag_engine.extract_text(str(test_file))
    assert "Hello world content" in txt
    assert len(rag_engine._extracted_text_cache) == 1

    # Simulate expired TTL
    key = next(iter(rag_engine._extracted_text_cache.keys()))
    with rag_engine._extracted_text_lock:
        rag_engine._extracted_text_cache[key] = (time.monotonic() - 150.0, "stale text")

    txt2 = rag_engine.extract_text(str(test_file))
    assert "Hello world content" in txt2

    # Reject text > 200 KB
    large_file = tmp_path / "large.txt"
    large_file.write_text("X" * (250 * 1024), encoding="utf-8")
    rag_engine.extract_text(str(large_file))
    large_key = (str(large_file.resolve()), large_file.stat().st_mtime)
    with rag_engine._extracted_text_lock:
        assert large_key not in rag_engine._extracted_text_cache


def test_rag_token_doc_freq_capping():
    with rag_engine._idf_lock:
        rag_engine._token_doc_freq.clear()
        for i in range(16000):
            rag_engine._token_doc_freq[f"tok_{i}"] = i

    rag_engine._record_frame_tokens("new token sample")
    with rag_engine._idf_lock:
        assert len(rag_engine._token_doc_freq) <= 10001


def test_rag_flush_write_queues_cleans_up():
    with patch.object(rag_engine, "_reset_memvid") as mock_reset, \
         patch.object(rag_engine._executor, "shutdown") as mock_shutdown:
        rag_engine.flush_write_queues()
        mock_reset.assert_called_once()
        mock_shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_server_cleanup_shuts_down_bg_tasks():
    with patch.object(server._bg_tasks, "shutdown") as mock_shutdown, \
         patch.object(server, "shutdown_proactive_intelligence"):
        server._cleanup_all()
        mock_shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_server_api_overlay_action_pruning():
    server._recent_overlay_actions.clear()
    now = time.time()
    for i in range(70):
        server._recent_overlay_actions[f"key_{i}"] = now - 10.0  # older than 5.0s

    with server.app.test_request_context(
        "/api/overlay/action",
        json={"type": "ink_action", "part": "act-toggle-wifi"},
    ):
        server.api_overlay_action()
        # Should have pruned older entries
        assert len(server._recent_overlay_actions) < 64


def test_weather_cache_evicts_expired():
    tool_registry._WEATHER_CACHE.clear()
    now = time.time()
    tool_registry._WEATHER_CACHE["old_city"] = (now - 400.0, {"temp": 20})
    tool_registry._WEATHER_CACHE["fresh_city"] = (now - 50.0, {"temp": 25})

    with patch.object(tool_registry, "get_weather_data", return_value={"temp": 30}):
        tool_registry._cached_weather_data("new_city")
        assert "old_city" not in tool_registry._WEATHER_CACHE
        assert "fresh_city" in tool_registry._WEATHER_CACHE
        assert "new_city" in tool_registry._WEATHER_CACHE


def test_found_files_capped_at_five():
    paths = [f"/path/to/file_{i}.txt" for i in range(10)]
    tool_registry.update_active_state("found_files", paths)
    state = tool_registry.get_active_state()
    found = state.get("found_files", {}).get("items", [])
    assert len(found) == 5
    assert found == paths[:5]
