from __future__ import annotations

import pytest

from app.services.rag_service import RAGService
from app.services.xhs_spider import XHSPost
from tests.acceptance.conftest import write_acceptance_artifact


def _sample_post(note_id: str, title: str, content: str) -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=title,
        content=content,
        author="acceptance",
        tags=["护肤", "修护"],
        liked_count=120,
        collected_count=60,
        comment_count=12,
        share_count=8,
        note_url=f"https://example.com/{note_id}",
        images=[],
    )


@pytest.mark.acceptance
@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_rag_roundtrip(
    acceptance_enabled: None,
    rag_ready: None,
    acceptance_storage,
    acceptance_artifact_dir,
):
    rag = RAGService(persist_dir=acceptance_storage["chroma_dir"])
    session_id = "acceptance-rag"
    posts = [
        _sample_post("n1", "敏感肌修护精华", "屏障受损时怎么选修护精华"),
        _sample_post("n2", "换季泛红急救", "换季泛红时的护肤步骤和避坑"),
    ]

    try:
        quality = await rag.index_documents(session_id, posts, "敏感肌修护")
        similar = await rag.query_similar(session_id, "修护精华适合泛红敏感肌吗", top_k=2)
        stats = await rag.get_collection_stats(session_id)
    except Exception as exc:
        pytest.skip(f"embedding model unavailable for acceptance rag test: {exc}")

    assert quality.total_notes == 2
    assert 0.0 <= quality.score <= 1.0
    assert similar
    assert all(item.note_id for item in similar)
    assert stats["session_id"] == session_id
    assert stats["document_count"] >= 2

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "real_rag_roundtrip",
        {
            "session_id": session_id,
            "quality_score": quality.score,
            "avg_similarity": quality.avg_similarity,
            "top_similar_note_id": similar[0].note_id,
            "document_count": stats["document_count"],
            "embedding_model": rag.embedding_model,
        },
    )
