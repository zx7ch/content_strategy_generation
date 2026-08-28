"""Research-only sentence embedding boundary for frozen evidence snapshots."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

INPUT_FORMAT_VERSION = "research_note_title_body_v1"
_WARMUP_TITLE = "Research embedding 预热"
_WARMUP_BODY = "验证中文语义向量"


@dataclass(frozen=True)
class ResearchEmbeddingDocument:
    note_id: str
    title: str
    body: str

    def __post_init__(self) -> None:
        if not self.note_id.strip():
            raise ValueError("research embedding document requires note_id")


@dataclass(frozen=True)
class ResearchEmbeddingFingerprint:
    provider: str
    model: str
    revision: str
    dimensions: int
    normalization: str = "l2"
    input_format_version: str = INPUT_FORMAT_VERSION

    def as_dict(self) -> dict[str, str | int]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "dimensions": self.dimensions,
            "normalization": self.normalization,
            "input_format_version": self.input_format_version,
        }


@dataclass(frozen=True)
class ResearchEmbeddingHealth:
    status: str
    fingerprint: ResearchEmbeddingFingerprint
    error_code: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "fingerprint": self.fingerprint.as_dict(),
        }
        if self.error_code is not None:
            result["error_code"] = self.error_code
        if self.message is not None:
            result["message"] = self.message
        return result


@dataclass(frozen=True)
class ResearchEmbeddingBatch:
    document_ids: tuple[str, ...]
    input_fingerprints: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]
    embedding_fingerprint: ResearchEmbeddingFingerprint


ModelLoader = Callable[[str, str], Any]


class ResearchEmbeddingUnavailableError(RuntimeError):
    """Safe failure exposed when Research embedding cannot serve analysis."""


def _default_model_loader(model_name: str, revision: str) -> Any:
    from sentence_transformers import SentenceTransformer

    kwargs = {"revision": revision} if revision else {}
    return SentenceTransformer(model_name, **kwargs)


def _format_document(document: ResearchEmbeddingDocument) -> str:
    return f"标题：{document.title.strip()}\n正文：{document.body.strip()}"


def _input_fingerprint(text: str) -> str:
    return hashlib.sha256(f"{INPUT_FORMAT_VERSION}\x1f{text}".encode()).hexdigest()


class SentenceTransformerResearchEmbeddingAdapter:
    """Validate and expose one stable Research embedding contract."""

    def __init__(
        self,
        *,
        model_name: str,
        model_revision: str,
        expected_dimensions: int,
        model_loader: ModelLoader = _default_model_loader,
    ) -> None:
        if not model_name.strip() or not model_revision.strip():
            raise ValueError("research embedding model name and revision are required")
        if expected_dimensions < 1:
            raise ValueError("research embedding dimensions must be positive")
        self._fingerprint = ResearchEmbeddingFingerprint(
            provider="sentence_transformers",
            model=model_name,
            revision=model_revision,
            dimensions=expected_dimensions,
        )
        self._model_loader = model_loader
        self._model: Any | None = None

    @property
    def fingerprint(self) -> ResearchEmbeddingFingerprint:
        return self._fingerprint

    def warm(self) -> ResearchEmbeddingHealth:
        model = self._model_loader(
            self._fingerprint.model,
            self._fingerprint.revision,
        )
        warmup = _format_document(
            ResearchEmbeddingDocument("__warmup__", _WARMUP_TITLE, _WARMUP_BODY)
        )
        self._encode(model, (warmup,))
        self._model = model
        return ResearchEmbeddingHealth("ready", self._fingerprint)

    def embed_documents(
        self,
        documents: Sequence[ResearchEmbeddingDocument],
    ) -> ResearchEmbeddingBatch:
        if self._model is None:
            raise RuntimeError("RESEARCH_EMBEDDING_UNAVAILABLE")
        if not documents:
            raise ValueError("research embedding requires at least one document")
        document_ids = tuple(document.note_id for document in documents)
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("research embedding note_ids must be unique")
        texts = tuple(_format_document(document) for document in documents)
        vectors = self._encode(self._model, texts)
        return ResearchEmbeddingBatch(
            document_ids=document_ids,
            input_fingerprints=tuple(_input_fingerprint(text) for text in texts),
            vectors=vectors,
            embedding_fingerprint=self._fingerprint,
        )

    def close(self) -> None:
        self._model = None

    def _encode(
        self,
        model: Any,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        encoded = model.encode(
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        rows = tuple(tuple(float(value) for value in row) for row in encoded)
        if len(rows) != len(texts):
            raise RuntimeError("RESEARCH_EMBEDDING_RESULT_COUNT_MISMATCH")
        if any(len(row) != self._fingerprint.dimensions for row in rows):
            raise RuntimeError("RESEARCH_EMBEDDING_DIMENSION_MISMATCH")
        if any(not math.isfinite(value) for row in rows for value in row):
            raise RuntimeError("RESEARCH_EMBEDDING_NON_FINITE")
        return rows


class ResearchEmbeddingRuntime:
    """Own adapter readiness while allowing the application to start degraded."""

    def __init__(self, adapter: SentenceTransformerResearchEmbeddingAdapter) -> None:
        self._adapter = adapter
        self._lock = threading.Lock()
        self._stopped = False
        self._starting = False
        self._start_event = threading.Event()
        self._health = ResearchEmbeddingHealth("loading", adapter.fingerprint)

    @property
    def health(self) -> ResearchEmbeddingHealth:
        with self._lock:
            return self._health

    def start(self) -> ResearchEmbeddingHealth:
        with self._lock:
            if self._stopped:
                return self._health
            if self._health.status == "ready":
                return self._health
            if self._starting:
                start_event = self._start_event
                should_start = False
            else:
                self._starting = True
                self._start_event = threading.Event()
                start_event = self._start_event
                should_start = True
        if not should_start:
            start_event.wait()
            return self.health
        try:
            health = self._adapter.warm()
        except Exception:  # external model loading failures are projected safely
            health = ResearchEmbeddingHealth(
                status="unavailable",
                fingerprint=self._adapter.fingerprint,
                error_code="RESEARCH_EMBEDDING_UNAVAILABLE",
                message="调研分析向量模型暂时不可用",
            )
        with self._lock:
            self._starting = False
            if self._stopped:
                self._adapter.close()
                start_event.set()
                return self._health
            self._health = health
            start_event.set()
            return self._health

    def embed_documents(
        self,
        documents: Sequence[ResearchEmbeddingDocument],
    ) -> ResearchEmbeddingBatch:
        with self._lock:
            if self._health.status != "ready" or self._stopped:
                raise ResearchEmbeddingUnavailableError("RESEARCH_EMBEDDING_UNAVAILABLE")
            try:
                return self._adapter.embed_documents(documents)
            except RuntimeError as exc:
                text = str(exc)
                code = (
                    text
                    if text.startswith("RESEARCH_EMBEDDING_")
                    else "RESEARCH_EMBEDDING_UNAVAILABLE"
                )
                self._health = ResearchEmbeddingHealth(
                    status="unavailable",
                    fingerprint=self._adapter.fingerprint,
                    error_code=code,
                    message="调研分析向量模型暂时不可用",
                )
                raise ResearchEmbeddingUnavailableError(code) from exc

    def stop(self) -> ResearchEmbeddingHealth:
        with self._lock:
            self._stopped = True
            if self._starting:
                self._start_event.set()
            self._adapter.close()
            self._health = ResearchEmbeddingHealth(
                status="unavailable",
                fingerprint=self._adapter.fingerprint,
                error_code="RESEARCH_EMBEDDING_STOPPED",
                message="调研分析向量模型已停止",
            )
            return self._health


def build_research_embedding_runtime(
    settings: Any,
    *,
    model_loader: ModelLoader = _default_model_loader,
) -> ResearchEmbeddingRuntime:
    """Build the Research-only runtime from its independent configuration."""
    return ResearchEmbeddingRuntime(
        SentenceTransformerResearchEmbeddingAdapter(
            model_name=str(settings.CONTENT_RESEARCH_EMBEDDING_MODEL),
            model_revision=str(settings.CONTENT_RESEARCH_EMBEDDING_REVISION),
            expected_dimensions=int(settings.CONTENT_RESEARCH_EMBEDDING_DIMENSIONS),
            model_loader=model_loader,
        )
    )
