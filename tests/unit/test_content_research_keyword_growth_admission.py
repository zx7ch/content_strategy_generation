from __future__ import annotations

import pytest

from app.content_research.admission.candidates import ExtractedFact
from app.content_research.admission.keyword_growth import build_keyword_growth_candidate

CONTEXT = {"keyword_patterns": ("轻量",), "source_published_at": "2026-07-17T00:00:00+00:00", "reference_window": {"non_overlapping": True, "comparable": True, "bias_disclosure": "search bias", "recent_eligible": 12, "reference_eligible": 30, "recent_keyword_count": 6, "reference_keyword_count": 4}}


def _fact(text="轻量通勤装备"):
    return ExtractedFact("run", "keyword_growth", "dep", "content_text", text, "https://example")


def test_keyword_factory_allows_current_pattern_and_comparable_window_growth():
    current = build_keyword_growth_candidate(workflow_run_id="run", direction_id="keyword_growth", claim_type="sampled_keyword_pattern", keyword="轻量", fact=_fact(), context=CONTEXT)
    growth = build_keyword_growth_candidate(workflow_run_id="run", direction_id="keyword_growth", claim_type="keyword_growth_with_comparable_baseline", keyword="轻量", fact=_fact(), context=CONTEXT)
    assert current.intent_id == "keyword_discovery"
    assert growth.intent_id == "relative_window_comparison"


def test_keyword_factory_rejects_insufficient_reference_window_but_keeps_current_pattern_possible():
    incomplete = {**CONTEXT, "reference_window": {"non_overlapping": True, "comparable": False, "recent_eligible": 12}}
    current = build_keyword_growth_candidate(workflow_run_id="run", direction_id="keyword_growth", claim_type="sampled_keyword_pattern", keyword="轻量", fact=_fact(), context=incomplete)
    assert current.claim_type == "sampled_keyword_pattern"
    with pytest.raises(ValueError, match="reference_window_insufficient"):
        build_keyword_growth_candidate(workflow_run_id="run", direction_id="keyword_growth", claim_type="keyword_growth_with_comparable_baseline", keyword="轻量", fact=_fact(), context=incomplete)


def test_keyword_factory_requires_literal_quote_not_metrics_or_inferred_keyword():
    with pytest.raises(ValueError, match="literal keyword quote"):
        build_keyword_growth_candidate(workflow_run_id="run", direction_id="keyword_growth", claim_type="sampled_keyword_pattern", keyword="轻量", fact=_fact("通勤装备"), context=CONTEXT)
