from unittest.mock import patch

from amigo.core import rag_engine
from amigo.utils import tool_registry


def test_build_document_evidence_cites_retrieved_file_and_passage():
    results = [
        {
            "text": "The project deadline is October 30.",
            "score": 0.82,
            "raw_semantic": 0.77,
            "lexical_overlap": 0.5,
            "source": rag_engine.DOCUMENTS,
            "metadata": {
                "filepath": r"C:\workspace\planning\roadmap.pdf",
                "filename": "roadmap.pdf",
                "chunk_index": 4,
            },
        },
        {
            "text": "Unrelated content.",
            "score": 0.05,
            "raw_semantic": 0.2,
            "lexical_overlap": 0.0,
            "source": rag_engine.DOCUMENTS,
            "metadata": {
                "filepath": r"C:\workspace\archive\notes.docx",
                "filename": "notes.docx",
                "chunk_index": 0,
            },
        },
    ]
    with patch.object(rag_engine, "search", return_value=results):
        context, sources = rag_engine.build_document_evidence("When is the deadline?")

    assert "roadmap.pdf, passage 5" in context
    assert "The project deadline is October 30." in context
    assert "notes.docx" not in context
    assert sources == [{"id": 1, "file": "roadmap.pdf", "location": "passage 5"}]


def test_document_qa_appends_only_retrieved_source_locations():
    sources = [{"id": 1, "file": "meeting-notes.docx", "location": "passage 2"}]
    with (
        patch.object(tool_registry.rag_engine, "resolve_contextual_query", return_value="question"),
        patch.object(
            tool_registry.rag_engine,
            "build_document_evidence",
            return_value=("Evidence with [Source 1: meeting-notes.docx, passage 2]", sources),
        ),
        patch.object(tool_registry, "get_ai_response", return_value="The meeting is at noon.") as answer,
    ):
        response, _, metadata = tool_registry._tool_ask_document(
            {"query": "question"}, "question", ""
        )

    answer.assert_called_once()
    assert response == "The meeting is at noon.\n\nSources: [1] meeting-notes.docx — passage 2"
    assert metadata["sources"] == sources


def test_document_qa_abstains_when_no_grounded_passages_are_found():
    with (
        patch.object(tool_registry.rag_engine, "resolve_contextual_query", return_value="question"),
        patch.object(tool_registry.rag_engine, "build_document_evidence", return_value=("", [])),
        patch.object(tool_registry, "get_ai_response") as answer,
    ):
        response, _, metadata = tool_registry._tool_ask_document(
            {"query": "question"}, "question", ""
        )

    answer.assert_not_called()
    assert "couldn't find enough relevant information" in response
    assert metadata["status"] == "insufficient_evidence"


def test_tts_normalizer_silences_sources_block_and_citations():
    from amigo.utils.tts import normalize_text_for_tts

    full_response = (
        "Your SSC registration number is 10032417783.\n\n"
        "Let me know if you need anything else!\n\n"
        "Sources: [1] ssc admit card.pdf — passage 2; [2] ssc admit card.pdf — passage 3"
    )
    spoken = normalize_text_for_tts(full_response)
    assert "Sources" not in spoken
    assert "admit card" not in spoken
    assert "passage" not in spoken
    assert "registration number" in spoken


def test_is_chunk_grounded_documents():
    # score >= 0.28 and (lex >= 0.18 or salient >= 0.40)
    chunk_accepted_lex = {
        "source": rag_engine.DOCUMENTS,
        "score": 0.29,
        "lexical_overlap": 0.19,
        "salient_coverage": 0.0,
    }
    assert rag_engine._is_chunk_grounded(chunk_accepted_lex, floor=0.34) is True

    chunk_accepted_salient = {
        "source": rag_engine.DOCUMENTS,
        "score": 0.28,
        "lexical_overlap": 0.05,
        "salient_coverage": 0.42,
    }
    assert rag_engine._is_chunk_grounded(chunk_accepted_salient, floor=0.34) is True

    chunk_rejected_score = {
        "source": rag_engine.DOCUMENTS,
        "score": 0.27,
        "lexical_overlap": 0.50,
        "salient_coverage": 0.50,
    }
    assert rag_engine._is_chunk_grounded(chunk_rejected_score, floor=0.34) is False

    chunk_rejected_lex_sal = {
        "source": rag_engine.DOCUMENTS,
        "score": 0.35,
        "lexical_overlap": 0.10,
        "salient_coverage": 0.30,
    }
    assert rag_engine._is_chunk_grounded(chunk_rejected_lex_sal, floor=0.34) is False


def test_build_document_evidence_retries_without_metadata_filter():
    results_empty = []
    results_all = [
        {
            "text": "User has B.Tech in Computer Science.",
            "score": 0.35,
            "lexical_overlap": 0.20,
            "salient_coverage": 0.45,
            "source": rag_engine.DOCUMENTS,
            "metadata": {
                "filepath": r"C:\workspace\docs\cv.pdf",
                "filename": "cv.pdf",
                "chunk_index": 0,
            },
        }
    ]

    calls = []

    def mock_search(query, target_collections=None, top_k=30, metadata_filter=None, conversation_history=None):
        calls.append(metadata_filter)
        if metadata_filter is not None:
            return results_empty
        return results_all

    with patch.object(rag_engine, "search", side_effect=mock_search):
        context, sources = rag_engine.build_document_evidence("What is my qualification?", filepath=r"C:\wrong\path.pdf")

    assert len(calls) == 2
    assert calls[0] == {"filepath": r"C:\wrong\path.pdf"}
    assert calls[1] is None
    assert "cv.pdf, passage 1" in context
    assert sources == [{"id": 1, "file": "cv.pdf", "location": "passage 1"}]


def test_tool_ask_document_resolves_filepath_via_search_files_by_context():
    matched = [{"filepath": r"C:\docs\resume.pdf", "score": 0.40}]
    sources = [{"id": 1, "file": "resume.pdf", "location": "passage 1"}]

    with (
        patch.object(tool_registry, "resolve_document_path", return_value=None),
        patch.object(tool_registry.rag_engine, "search_files_by_context", return_value=matched) as mock_ctx_search,
        patch.object(tool_registry.os.path, "exists", return_value=True),
        patch.object(
            tool_registry.rag_engine,
            "build_document_evidence",
            return_value=("Evidence from resume", sources),
        ) as mock_build_ev,
        patch.object(tool_registry, "get_ai_response", return_value="Here is your resume answer.") as answer,
    ):
        response, _, metadata = tool_registry._tool_ask_document(
            {"filepath": "resume", "question": "What is my GPA?"},
            "What is my GPA?",
            "",
            conversation_history=[],
        )

    mock_ctx_search.assert_called_once_with("resume", top_k=1)
    # verify search_q was not polluted with filepath
    mock_build_ev.assert_called_once_with(
        "What is my GPA?",
        top_k=5,
        conversation_history=[],
        filepath=r"C:\docs\resume.pdf",
    )
    answer.assert_called_once()
    assert "Here is your resume answer." in response


def test_init_rag_triggers_reindex_when_documents_zero():
    mock_indexer = patch("amigo.core.rag_indexer.get_indexer").start()
    indexer_instance = mock_indexer.return_value

    with (
        patch.object(rag_engine, "init_memvid"),
        patch.object(rag_engine, "get_index_stats", return_value={"documents": 0}),
        patch("threading.Thread") as mock_thread,
    ):
        rag_engine.init_rag(background=False)
        mock_thread.assert_called_once()
        assert mock_thread.call_args[1]["target"] == indexer_instance.full_index
        assert mock_thread.call_args[1]["kwargs"] == {"force": True}

    patch.stopall()


def test_bm25_normalization():
    raw_score = 2.0
    bm25_norm = raw_score / (raw_score + 2.0)
    assert bm25_norm == 0.5
