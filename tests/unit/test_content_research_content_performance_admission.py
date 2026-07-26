from __future__ import annotations

import pytest

from app.content_research.admission.candidates import ExtractedFact
from app.content_research.admission.content_performance import (
    CONTENT_PERFORMANCE_CLAIM_INTENTS,
    build_content_performance_candidate,
    content_performance_boundary_reason,
)
from app.content_research.admission.evaluator import ClaimAdmissionEvaluator
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

ENGAGEMENT_CONTEXT = {"metrics": {"like_count": 120}, "metrics_observed_at": "2026-07-18T00:00:00+00:00"}


def _fact(field_path: str = "content_text", text: str = "通勤场景的清单式开头") -> ExtractedFact:
    return ExtractedFact("run_1", "content_performance", "dep_1", field_path, text, "https://example/note")


@pytest.mark.parametrize("claim_type,intent", CONTENT_PERFORMANCE_CLAIM_INTENTS.items())
def test_content_performance_factory_maps_claims_and_keeps_metrics_as_context(claim_type, intent):
    candidate = build_content_performance_candidate(
        workflow_run_id="run_1",
        direction_id="content_performance",
        claim_type=claim_type,
        fact=_fact(),
        engagement_context=ENGAGEMENT_CONTEXT,
    )

    assert candidate.intent_id == intent
    assert candidate.payload["quote_refs"][0]["field_path"] == "content_text"
    assert candidate.payload["scope"]["engagement_context"] == ENGAGEMENT_CONTEXT


@pytest.mark.parametrize("field_path", ["metrics", "tags", "comment_text", "media"])
def test_content_performance_factory_rejects_non_textual_or_comment_evidence(field_path):
    with pytest.raises(ValueError, match="must cite title or content text"):
        build_content_performance_candidate(
            workflow_run_id="run_1",
            direction_id="content_performance",
            claim_type="visible_content_format",
            fact=_fact(field_path),
            engagement_context=ENGAGEMENT_CONTEXT,
        )


@pytest.mark.parametrize("text", ["这个格式表现更好", "导致互动更高", "提升效果", "点击转化更高"])
def test_content_performance_factory_rejects_causal_click_and_conversion_outcomes(text):
    with pytest.raises(ValueError, match="cannot claim an interaction effect"):
        build_content_performance_candidate(
            workflow_run_id="run_1",
            direction_id="content_performance",
            claim_type="visible_content_format",
            fact=_fact(text=text),
            engagement_context=ENGAGEMENT_CONTEXT,
        )


def test_evaluator_rejects_metric_only_content_performance_candidate():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1"
    )
    contract = next(item for item in contracts if item.direction_id == "content_performance")
    policy = next(item for item in policies if item.direction_id == "content_performance")
    packet = DirectionalEvidencePacketRecord(
        "dep_1", "v1", {"field_availability": {field: "present" for field in contract.required_note_fields}},
        workflow_run_id="run_1", research_direction_id="content_performance", canonical_source_id="cs_1", field_projection_hash="packet-hash",
    )
    candidate = ClaimCandidateRecord(
        "cc_metric", "v2", {"scope": {"sample": "selected_packets", "engagement_context": ENGAGEMENT_CONTEXT}, "quote_refs": [{"field_path": "metrics"}]},
        workflow_run_id="run_1", research_direction_id="content_performance", evidence_packet_id="dep_1",
        statement="点赞 120", intent_id="engagement_cohort", claim_type="observed_high_engagement_sample",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate, packet=packet, contract=contract, sample_policy=policy,
        policy_snapshot=snapshot, selected_source_count=10, independent_author_count=10,
    ).record

    assert content_performance_boundary_reason(candidate) == "content_performance_evidence_boundary_violation"
    assert result.decision == "rejected"


def test_missing_snapshot_fields_still_downgrade_an_otherwise_valid_candidate():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1"
    )
    contract = next(item for item in contracts if item.direction_id == "content_performance")
    policy = next(item for item in policies if item.direction_id == "content_performance")
    candidate = build_content_performance_candidate(
        workflow_run_id="run_1", direction_id="content_performance", claim_type="visible_content_format",
        fact=_fact(), engagement_context=ENGAGEMENT_CONTEXT,
    )
    packet = DirectionalEvidencePacketRecord(
        "dep_1", "v1", {"field_availability": {}}, workflow_run_id="run_1",
        research_direction_id="content_performance", canonical_source_id="cs_1", field_projection_hash="packet-hash",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate, packet=packet, contract=contract, sample_policy=policy,
        policy_snapshot=snapshot, selected_source_count=0, independent_author_count=0,
    ).record

    assert result.decision == "downgraded"
    assert "blocking_field_missing" in result.payload["reason_codes"]
