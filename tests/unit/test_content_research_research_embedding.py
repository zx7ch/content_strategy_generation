from __future__ import annotations

import numpy as np
import pytest

from app.content_research.research_embedding import (
    ResearchEmbeddingDocument,
    ResearchEmbeddingRuntime,
    ResearchEmbeddingUnavailableError,
    SentenceTransformerResearchEmbeddingAdapter,
    build_research_embedding_runtime,
)


class RecordingSentenceTransformer:
    def __init__(self) -> None:
        self.encoded_texts: list[str] = []

    def encode(
        self,
        texts: list[str],
        *,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
    ) -> np.ndarray:
        assert convert_to_numpy is True
        assert normalize_embeddings is True
        self.encoded_texts.extend(texts)
        vectors = [[1.0, 0.0, 0.0], [0.0, 0.6, 0.8]]
        return np.asarray(vectors[: len(texts)], dtype=np.float32)


class ScriptedSentenceTransformer:
    def __init__(self, result: np.ndarray) -> None:
        self._result = result
        self._call_count = 0

    def encode(
        self,
        texts: list[str],
        *,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
    ) -> np.ndarray:
        self._call_count += 1
        if self._call_count == 1:
            return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)
        return self._result


def test_research_embedding_adapter_formats_snapshot_fields_and_exposes_fingerprint() -> None:
    model = RecordingSentenceTransformer()
    load_calls: list[tuple[str, str]] = []

    def load_model(model_name: str, revision: str) -> RecordingSentenceTransformer:
        load_calls.append((model_name, revision))
        return model

    adapter = SentenceTransformerResearchEmbeddingAdapter(
        model_name="research-model",
        model_revision="revision-7",
        expected_dimensions=3,
        model_loader=load_model,
    )

    health = adapter.warm()
    result = adapter.embed_documents(
        (
            ResearchEmbeddingDocument("note-1", "  凉感衬衫  ", " 夏季通勤不闷 "),
            ResearchEmbeddingDocument("note-2", "防晒", "户外走路后背会贴身"),
        )
    )

    assert health.status == "ready"
    assert health.warmup_duration_ms is not None
    assert health.warmup_duration_ms >= 0
    assert health.as_dict()["warmup_duration_ms"] == health.warmup_duration_ms
    assert load_calls == [("research-model", "revision-7")]
    assert model.encoded_texts == [
        "标题：Research embedding 预热\n正文：验证中文语义向量",
        "标题：凉感衬衫\n正文：夏季通勤不闷",
        "标题：防晒\n正文：户外走路后背会贴身",
    ]
    assert adapter.fingerprint.as_dict() == {
        "provider": "sentence_transformers",
        "model": "research-model",
        "revision": "revision-7",
        "dimensions": 3,
        "normalization": "l2",
        "input_format_version": "research_note_title_body_v1",
    }
    assert result.document_ids == ("note-1", "note-2")
    assert result.vectors == ((1.0, 0.0, 0.0), (0.0, 0.6000000238418579, 0.800000011920929))
    assert len(result.input_fingerprints) == 2


@pytest.mark.parametrize(
    ("result", "error_code"),
    [
        (
            np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
            "RESEARCH_EMBEDDING_RESULT_COUNT_MISMATCH",
        ),
        (
            np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            "RESEARCH_EMBEDDING_DIMENSION_MISMATCH",
        ),
        (
            np.asarray([[1.0, 0.0, 0.0], [0.0, np.nan, 0.0]], dtype=np.float32),
            "RESEARCH_EMBEDDING_NON_FINITE",
        ),
        (
            np.asarray([[2.0, 0.0, 0.0], [0.0, 0.6, 0.8]], dtype=np.float32),
            "RESEARCH_EMBEDDING_NOT_NORMALIZED",
        ),
    ],
)
def test_research_embedding_adapter_rejects_invalid_model_results(
    result: np.ndarray,
    error_code: str,
) -> None:
    model = ScriptedSentenceTransformer(result)
    adapter = SentenceTransformerResearchEmbeddingAdapter(
        model_name="research-model",
        model_revision="revision-7",
        expected_dimensions=3,
        model_loader=lambda _name, _revision: model,
    )
    adapter.warm()

    with pytest.raises(RuntimeError, match=error_code):
        adapter.embed_documents(
            (
                ResearchEmbeddingDocument("note-1", "标题一", "正文一"),
                ResearchEmbeddingDocument("note-2", "标题二", "正文二"),
            )
        )


def test_research_embedding_runtime_starts_degraded_without_leaking_loader_error() -> None:
    def fail_to_load(_model_name: str, _revision: str) -> RecordingSentenceTransformer:
        raise RuntimeError("download failed with token=secret-value")

    adapter = SentenceTransformerResearchEmbeddingAdapter(
        model_name="research-model",
        model_revision="revision-7",
        expected_dimensions=3,
        model_loader=fail_to_load,
    )
    runtime = ResearchEmbeddingRuntime(adapter)

    health = runtime.start()

    assert health.as_dict() == {
        "status": "unavailable",
        "error_code": "RESEARCH_EMBEDDING_UNAVAILABLE",
        "message": "调研分析向量模型暂时不可用",
        "fingerprint": adapter.fingerprint.as_dict(),
    }
    assert "secret-value" not in str(health.as_dict())
    with pytest.raises(
        ResearchEmbeddingUnavailableError,
        match="RESEARCH_EMBEDDING_UNAVAILABLE",
    ):
        runtime.embed_documents(
            (ResearchEmbeddingDocument("note-1", "标题", "正文"),)
        )


def test_runtime_factory_uses_dedicated_research_embedding_configuration() -> None:
    class ResearchEmbeddingSettings:
        CONTENT_RESEARCH_EMBEDDING_MODEL = "research-model"
        CONTENT_RESEARCH_EMBEDDING_REVISION = "revision-7"
        CONTENT_RESEARCH_EMBEDDING_DIMENSIONS = 3

    model = RecordingSentenceTransformer()
    load_calls: list[tuple[str, str]] = []

    def load_model(model_name: str, revision: str) -> RecordingSentenceTransformer:
        load_calls.append((model_name, revision))
        return model

    runtime = build_research_embedding_runtime(
        ResearchEmbeddingSettings(),
        model_loader=load_model,
    )

    health = runtime.start()
    replayed_health = runtime.start()

    assert health.status == "ready"
    assert replayed_health == health
    assert health.fingerprint.model == "research-model"
    assert load_calls == [("research-model", "revision-7")]

    stopped = runtime.stop()
    assert stopped.status == "unavailable"
    assert stopped.error_code == "RESEARCH_EMBEDDING_STOPPED"
    with pytest.raises(ResearchEmbeddingUnavailableError):
        runtime.embed_documents(
            (ResearchEmbeddingDocument("note-1", "标题", "正文"),)
        )
