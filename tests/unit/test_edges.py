from __future__ import annotations

import pytest

from app.graph.edges import should_expand_query, should_regenerate


@pytest.mark.parametrize(
    ("quality_score", "doc_count", "expected"),
    [
        (0.34, 9, True),
        (0.35, 9, False),
        (0.34, 10, False),
        (0.50, 3, False),
    ],
)
def test_should_expand_query_respects_thresholds(quality_score, doc_count, expected):
    assert should_expand_query(quality_score, doc_count) is expected


@pytest.mark.parametrize(
    ("embedding_similarity", "lexical_overlap", "expected"),
    [
        (0.61, 0.0, "retry"),
        (0.60, 0.0, "warn"),
        (0.31, 0.0, "warn"),
        (0.30, 0.0, "accept"),
        (0.10, 0.41, "warn"),
        (0.10, 0.40, "accept"),
    ],
)
def test_should_regenerate_returns_expected_decision(
    embedding_similarity, lexical_overlap, expected
):
    assert should_regenerate(embedding_similarity, lexical_overlap) == expected


def test_should_regenerate_prioritizes_retry_over_warning():
    assert should_regenerate(0.91, 0.95) == "retry"
