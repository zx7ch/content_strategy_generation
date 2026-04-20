"""RAG Service - single collection + composite IDs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from app.models.schemas import RAGDocument
from app.services.xhs_spider import XHSPost


@dataclass
class QualityScore:
    """RAG quality score result."""

    score: float
    total_notes: int
    filtered_count: int
    avg_similarity: float


@dataclass
class SimilarPost:
    """Similar post payload."""

    note_id: str
    title: str
    content: str
    tags: List[str]
    similarity: float


class RAGService:
    """RAG storage and retrieval backed by Chroma."""

    COLLECTION_NAME = "xhs_documents"
    EMBEDDING_DIM = 768

    def __init__(self, persist_dir: Optional[str] = None, embedding_model: Optional[str] = None):
        self.persist_dir = persist_dir or settings.CHROMA_PERSIST_DIR
        self.embedding_model = embedding_model or settings.RAG_EMBEDDING_MODEL
        self._client: Optional[chromadb.Client] = None
        self._embedder = None

    def _get_client(self) -> chromadb.Client:
        if self._client is None:
            persist_path = str(Path(self.persist_dir).expanduser())
            if hasattr(chromadb, "PersistentClient"):
                self._client = chromadb.PersistentClient(
                    path=persist_path,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            else:
                self._client = chromadb.Client(
                    ChromaSettings(
                        is_persistent=True,
                        persist_directory=persist_path,
                        anonymized_telemetry=False,
                    )
                )
        return self._client

    def _get_collection(self):
        client = self._get_client()
        if hasattr(client, "get_or_create_collection"):
            return client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        try:
            return client.get_collection(self.COLLECTION_NAME)
        except Exception:
            return client.create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )

    def _make_doc_id(self, session_id: str, note_id: str) -> str:
        return f"{session_id}:{note_id}"

    def _get_embedder(self):
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "RAG embedding dependency missing. Install `sentence-transformers`."
                ) from exc
            self._embedder = SentenceTransformer(self.embedding_model)
        return self._embedder

    def chunk_posts(self, posts: List[XHSPost]) -> List[RAGDocument]:
        docs: List[RAGDocument] = []
        for post in posts:
            first_paragraph = (post.content or "").split("\n\n", 1)[0].strip()
            content = first_paragraph or post.content or ""
            engagement_score = float(
                post.liked_count + 3 * post.collected_count + 5 * post.comment_count + 10 * post.share_count
            )
            docs.append(
                RAGDocument(
                    doc_id="",
                    session_id="",
                    note_id=post.note_id,
                    title=post.title,
                    content=content,
                    tags=post.tags,
                    engagement_score=engagement_score,
                )
            )
        return docs

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embeddings with the configured production model."""

        embedder = self._get_embedder()
        normalized_text = (text or "").strip() or " "
        vector = embedder.encode(
            [normalized_text],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )[0]
        if len(vector) != self.EMBEDDING_DIM:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {self.EMBEDDING_DIM}, got {len(vector)}"
            )
        return np.asarray(vector, dtype=np.float32).tolist()

    async def index_documents(self, session_id: str, posts: List[XHSPost], query: str) -> QualityScore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_index_documents, session_id, posts, query)

    def _sync_index_documents(self, session_id: str, posts: List[XHSPost], query: str) -> QualityScore:
        if not posts:
            return QualityScore(score=0.0, total_notes=0, filtered_count=0, avg_similarity=0.0)

        docs = self.chunk_posts(posts)
        collection = self._get_collection()

        ids: List[str] = []
        documents: List[str] = []
        embeddings: List[List[float]] = []
        metadatas: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for doc in docs:
            note_id = (doc.note_id or "").strip()
            if not note_id:
                continue
            doc_id = self._make_doc_id(session_id, note_id)
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            ids.append(doc_id)
            documents.append(f"标题: {doc.title}\n正文: {doc.content}\n标签: {', '.join(doc.tags)}")
            embeddings.append(self.generate_embedding(f"{doc.title}\n{doc.content}\n{query}"))
            metadatas.append(
                {
                    "session_id": session_id,
                    "note_id": note_id,
                    "title": doc.title,
                    "tags": ",".join(doc.tags),
                    "engagement_score": float(doc.engagement_score),
                }
            )

        if not ids:
            return QualityScore(score=0.0, total_notes=0, filtered_count=0, avg_similarity=0.0)

        # Idempotent write via upsert when available.
        if hasattr(collection, "upsert"):
            collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        else:
            try:
                collection.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
            except Exception:
                collection.update(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

        query_result = collection.query(
            query_embeddings=[self.generate_embedding(query)],
            n_results=min(10, len(ids)),
            where={"session_id": session_id},
            include=["distances", "metadatas"],
        )

        distances = query_result.get("distances", [[]])[0]
        similarities = [max(0.0, min(1.0, 1.0 - float(d))) for d in distances]
        avg_similarity = float(np.mean(similarities)) if similarities else 0.0

        metadata_items = query_result.get("metadatas", [[]])[0]
        engagements = [float(m.get("engagement_score", 0.0)) for m in metadata_items if m]
        if engagements:
            max_e = max(engagements)
            norm_engagement = float(np.mean([e / max_e if max_e > 0 else 0.0 for e in engagements]))
        else:
            norm_engagement = 0.0

        score = max(0.0, min(1.0, 0.6 * avg_similarity + 0.4 * norm_engagement))
        return QualityScore(
            score=score,
            total_notes=len(ids),
            filtered_count=len(metadata_items),
            avg_similarity=avg_similarity,
        )

    async def query_similar(self, session_id: str, content: str, top_k: int = 3) -> List[SimilarPost]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_query_similar, session_id, content, top_k)

    def _sync_query_similar(self, session_id: str, content: str, top_k: int) -> List[SimilarPost]:
        collection = self._get_collection()
        result = collection.query(
            query_embeddings=[self.generate_embedding(content)],
            n_results=max(1, top_k),
            where={"session_id": session_id},
            include=["metadatas", "documents", "distances"],
        )

        ids = result.get("ids", [[]])[0]
        if not ids:
            return []

        docs = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        items: List[SimilarPost] = []
        for i, _ in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            distance = float(distances[i]) if i < len(distances) else 1.0
            similarity = max(0.0, min(1.0, 1.0 - distance))
            tags_raw = meta.get("tags", "") if isinstance(meta, dict) else ""
            tags = tags_raw.split(",") if tags_raw else []
            items.append(
                SimilarPost(
                    note_id=meta.get("note_id", "") if isinstance(meta, dict) else "",
                    title=meta.get("title", "") if isinstance(meta, dict) else "",
                    content=docs[i] if i < len(docs) else "",
                    tags=tags,
                    similarity=similarity,
                )
            )
        return items

    async def calculate_similarity(self, session_id: str, note_content: str) -> float:
        similar = await self.query_similar(session_id, note_content, top_k=1)
        if not similar:
            return 0.0
        return float(similar[0].similarity)

    async def delete_collection(self, session_id: str) -> bool:
        """Keep method name for compatibility; delete documents by session."""

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_delete_collection, session_id)

    def _sync_delete_collection(self, session_id: str) -> bool:
        collection = self._get_collection()
        try:
            # Query ids first because delete(where=...) support varies by backend version.
            result = collection.get(where={"session_id": session_id}, include=[])
            ids = result.get("ids", []) if isinstance(result, dict) else []
            if not ids:
                return False
            collection.delete(ids=ids)
            return True
        except Exception:
            return False

    async def get_collection_stats(self, session_id: str) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_collection_stats, session_id)

    def _sync_get_collection_stats(self, session_id: str) -> Dict[str, Any]:
        collection = self._get_collection()
        result = collection.get(where={"session_id": session_id}, include=[])
        ids = result.get("ids", []) if isinstance(result, dict) else []
        return {
            "session_id": session_id,
            "collection_name": self.COLLECTION_NAME,
            "document_count": len(ids),
            "exists": True,
        }
