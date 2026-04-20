from __future__ import annotations

from typing import Literal

from app.config import settings


RegenerateDecision = Literal["retry", "warn", "accept"]


def should_expand_query(quality_score: float, doc_count: int) -> bool:
    return (
        float(quality_score) < settings.QUALITY_SCORE_THRESHOLD
        and int(doc_count) < settings.EXPANSION_DOC_COUNT_MAX
    )


def should_regenerate(
    embedding_similarity: float,
    lexical_overlap: float = 0.0,
) -> RegenerateDecision:
    similarity = float(embedding_similarity)
    lexical = float(lexical_overlap)
    if similarity > settings.EMBEDDING_REWRITE_THRESHOLD:
        return "retry"
    if (
        similarity > settings.EMBEDDING_WARNING_THRESHOLD
        or lexical > settings.LEXICAL_WARNING_THRESHOLD
    ):
        return "warn"
    return "accept"
