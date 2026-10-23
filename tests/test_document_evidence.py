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
