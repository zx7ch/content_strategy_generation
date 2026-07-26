from __future__ import annotations

import pytest

from app.content_research.admission.brand_activity import (
    BRAND_ACTIVITY_CLAIM_INTENTS,
    brand_activity_boundary_reason,
    build_brand_activity_candidate,
)
from app.content_research.admission.candidates import ExtractedFact
from app.content_research.admission.evaluator import ClaimAdmissionEvaluator
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

CONTEXT = {"activity_signals": tuple(BRAND_ACTIVITY_CLAIM_INTENTS), "source_published_at": "2026-07-17T00:00:00+00:00", "engagement_context": {"metrics": {"like_count": 12}, "metrics_observed_at": "2026-07-18T00:00:00+00:00"}}


def _fact(text: str = "夏日上新活动") -> ExtractedFact:
    return ExtractedFact("run_1", "brand_activity", "dep_1", "content_text", text, "https://example/note")


@pytest.mark.parametrize("claim_type,intent", BRAND_ACTIVITY_CLAIM_INTENTS.items())
def test_brand_activity_factory_maps_only_frozen_signal_types(claim_type, intent):
    candidate = build_brand_activity_candidate(workflow_run_id="run_1", direction_id="brand_activity", claim_type=claim_type, fact=_fact(), context=CONTEXT)
    assert candidate.intent_id == intent
    assert candidate.payload["scope"]["source_published_at"] == CONTEXT["source_published_at"]


@pytest.mark.parametrize("text", ["活动触达百万", "销量提升", "活动成功", "带来增长"])
def test_brand_activity_factory_rejects_outcome_claims(text):
    with pytest.raises(ValueError, match="cannot claim outcome or causal success"):
        build_brand_activity_candidate(workflow_run_id="run_1", direction_id="brand_activity", claim_type="campaign_signal", fact=_fact(text), context=CONTEXT)


def test_brand_activity_factory_requires_date_and_declared_signal():
    with pytest.raises(ValueError, match="requires publication date"):
        build_brand_activity_candidate(workflow_run_id="run_1", direction_id="brand_activity", claim_type="campaign_signal", fact=_fact(), context={**CONTEXT, "source_published_at": ""})
    with pytest.raises(ValueError, match="signal type is not allowed"):
        build_brand_activity_candidate(workflow_run_id="run_1", direction_id="brand_activity", claim_type="campaign_signal", fact=_fact(), context={**CONTEXT, "activity_signals": ("launch_signal",)})


def test_missing_brand_activity_fields_downgrade_valid_candidate():
    snapshot, policies, contracts = build_default_snapshot(snapshot_id="rps", workflow_run_id="run_1", brief_id="rb", plan_id="rp")
    contract = next(item for item in contracts if item.direction_id == "brand_activity")
    policy = next(item for item in policies if item.direction_id == "brand_activity")
    candidate = build_brand_activity_candidate(workflow_run_id="run_1", direction_id="brand_activity", claim_type="campaign_signal", fact=_fact(), context=CONTEXT)
    packet = DirectionalEvidencePacketRecord("dep_1", "v1", {"field_availability": {}}, workflow_run_id="run_1", research_direction_id="brand_activity", canonical_source_id="cs_1", field_projection_hash="hash")
    result = ClaimAdmissionEvaluator().evaluate(candidate=candidate, packet=packet, contract=contract, sample_policy=policy, policy_snapshot=snapshot, selected_source_count=0, independent_author_count=0).record
    assert result.decision == "downgraded"
    assert "blocking_field_missing" in result.payload["reason_codes"]


def test_evaluator_rejects_metric_only_brand_activity_candidate():
    snapshot, policies, contracts = build_default_snapshot(snapshot_id="rps", workflow_run_id="run_1", brief_id="rb", plan_id="rp")
    contract = next(item for item in contracts if item.direction_id == "brand_activity")
    policy = next(item for item in policies if item.direction_id == "brand_activity")
    candidate = ClaimCandidateRecord("cc", "v2", {"scope": {"sample": "selected_packets", **CONTEXT}, "quote_refs": [{"field_path": "metrics"}]}, workflow_run_id="run_1", research_direction_id="brand_activity", evidence_packet_id="dep", statement="活动", intent_id="activity_identification", claim_type="campaign_signal")
    packet = DirectionalEvidencePacketRecord("dep", "v1", {"field_availability": {field: "present" for field in contract.required_note_fields}}, workflow_run_id="run_1", research_direction_id="brand_activity", canonical_source_id="cs_1", field_projection_hash="hash")
    result = ClaimAdmissionEvaluator().evaluate(candidate=candidate, packet=packet, contract=contract, sample_policy=policy, policy_snapshot=snapshot, selected_source_count=3, independent_author_count=3).record
    assert brand_activity_boundary_reason(candidate) == "brand_activity_evidence_boundary_violation"
    assert result.decision == "rejected"
