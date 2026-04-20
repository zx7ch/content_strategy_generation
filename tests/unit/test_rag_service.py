from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pytest
import numpy as np

from app.config import settings
from app.services.rag_service import RAGService
from app.services.xhs_spider import XHSPost


@dataclass
class _QueryResult:
    ids: List[List[str]]
    documents: List[List[str]]
    metadatas: List[List[Dict[str, Any]]]
    distances: List[List[float]]


class FakeCollection:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.last_query_where = None

    def upsert(self, ids, documents, embeddings, metadatas):
        for i, doc_id in enumerate(ids):
            self.rows[doc_id] = {
                "id": doc_id,
                "document": documents[i],
                "embedding": embeddings[i],
                "metadata": metadatas[i],
            }

    def query(self, query_embeddings=None, n_results=3, where=None, include=None):
        self.last_query_where = where
        session_id = (where or {}).get("session_id")
        filtered = [r for r in self.rows.values() if r["metadata"].get("session_id") == session_id]
        filtered = filtered[:n_results]
        return {
            "ids": [[r["id"] for r in filtered]],
            "documents": [[r["document"] for r in filtered]],
            "metadatas": [[r["metadata"] for r in filtered]],
            "distances": [[0.2 for _ in filtered]],
        }

    def get(self, where=None, include=None):
        session_id = (where or {}).get("session_id")
        ids = [k for k, v in self.rows.items() if v["metadata"].get("session_id") == session_id]
        return {"ids": ids}

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()

    def get_or_create_collection(self, name, metadata=None):
        return self.collection


class FakeEmbedder:
    def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True):
        assert convert_to_numpy is True
        assert normalize_embeddings is True
        text = texts[0]
        vec = np.zeros((1, 768), dtype=np.float32)
        vec[0, 0] = min(1.0, len(text) / 100.0)
        vec[0, 1] = 0.5
        return vec


@pytest.fixture
def rag_service():
    rag = RAGService(persist_dir="/tmp/chroma-test")
    rag._client = FakeClient()
    rag._embedder = FakeEmbedder()
    return rag


def _post(note_id: str, title: str = "标题", content: str = "正文\n\n第二段") -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=title,
        content=content,
        author="author",
        tags=["护肤", "测评"],
        liked_count=10,
        collected_count=5,
        comment_count=2,
        share_count=1,
        note_url="https://xhs.test/note",
        images=["img"],
    )


def test_chunk_posts_extracts_first_paragraph(rag_service):
    docs = rag_service.chunk_posts([_post("n1", content="第一段\n\n第二段")])
    assert len(docs) == 1
    assert docs[0].note_id == "n1"
    assert docs[0].content == "第一段"


def test_generate_embedding_uses_model_output_and_dimension(rag_service):
    embedding = rag_service.generate_embedding("hello")
    assert len(embedding) == 768
    assert embedding[0] > 0
    assert rag_service.embedding_model == settings.RAG_EMBEDDING_MODEL


@pytest.mark.asyncio
async def test_index_documents_uses_composite_ids(rag_service):
    await rag_service.index_documents("s1", [_post("n1"), _post("n2")], "query")
    rows = rag_service._client.collection.rows
    assert "s1:n1" in rows
    assert "s1:n2" in rows
    assert len(rows["s1:n1"]["embedding"]) == 768


@pytest.mark.asyncio
async def test_query_similar_filters_by_session_id(rag_service):
    await rag_service.index_documents("s1", [_post("n1")], "query")
    await rag_service.index_documents("s2", [_post("n2")], "query")

    results = await rag_service.query_similar("s1", "content", top_k=5)
    assert results
    assert all(item.note_id == "n1" for item in results)
    assert rag_service._client.collection.last_query_where == {"session_id": "s1"}


@pytest.mark.asyncio
async def test_index_documents_quality_score_in_range(rag_service):
    score = await rag_service.index_documents("s1", [_post("n1")], "query")
    assert 0.0 <= score.score <= 1.0
    assert score.total_notes == 1


@pytest.mark.asyncio
async def test_index_documents_empty_posts_returns_zero(rag_service):
    score = await rag_service.index_documents("s1", [], "query")
    assert score.score == 0.0
    assert score.total_notes == 0


@pytest.mark.asyncio
async def test_delete_collection_deletes_only_session_docs(rag_service):
    await rag_service.index_documents("s1", [_post("n1")], "query")
    await rag_service.index_documents("s2", [_post("n2")], "query")

    deleted = await rag_service.delete_collection("s1")
    assert deleted is True
    assert "s1:n1" not in rag_service._client.collection.rows
    assert "s2:n2" in rag_service._client.collection.rows


@pytest.mark.asyncio
async def test_calculate_similarity_returns_top_similarity(rag_service):
    await rag_service.index_documents("s1", [_post("n1")], "query")
    similarity = await rag_service.calculate_similarity("s1", "some note")
    assert 0.0 <= similarity <= 1.0


@pytest.mark.asyncio
async def test_index_documents_skips_duplicate_and_empty_note_ids(rag_service):
    score = await rag_service.index_documents(
        "s1",
        [
            _post("n1", title="第一条"),
            _post("n1", title="重复条目"),
            _post("", title="缺失 note id"),
        ],
        "query",
    )

    rows = rag_service._client.collection.rows
    assert list(rows) == ["s1:n1"]
    assert rows["s1:n1"]["metadata"]["title"] == "第一条"
    assert score.total_notes == 1
