from __future__ import annotations

import pytest

from app.content_research.admission.candidates import ExtractedFact
from app.content_research.admission.competitor_discovery import (
    COMPETITOR_DISCOVERY_CLAIM_INTENTS,
    build_competitor_discovery_candidate,
    competitor_discovery_boundary_reason,
)
from app.content_research.admission.evaluator import ClaimAdmissionEvaluator
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

CONTEXT = {
    "competitor_names": ("竞品A",),
    "author": "作者甲",
    "canonical_source_id": "cs_1",
    "engagement_context": {"metrics": {"like_count": 120}, "metrics_observed_at": "2026-07-18T00:00:00+00:00"},
}


def _fact(field_path: str = "content_text", text: str = "竞品A 在通勤场景使用") -> ExtractedFact:
    return ExtractedFact("run_1", "competitor_discovery", "dep_1", field_path, text, "https://example/note")


@pytest.mark.parametrize("claim_type,intent", COMPETITOR_DISCOVERY_CLAIM_INTENTS.items())
def test_competitor_factory_maps_allowed_claims_to_a_direct_named_quote(claim_type, intent):
    candidate = build_competitor_discovery_candidate(
        workflow_run_id="run_1", direction_id="competitor_discovery", claim_type=claim_type,
        competitor_name="竞品A", fact=_fact(), context=CONTEXT,
    )

    assert candidate.intent_id == intent
    assert candidate.payload["scope"]["competitor_name"] == "竞品A"
    assert candidate.payload["scope"]["author"] == "作者甲"


@pytest.mark.parametrize("field_path", ["metrics", "comment_text", "media"])
def test_competitor_factory_rejects_non_quote_evidence(field_path):
    with pytest.raises(ValueError, match="must cite title, content text, or tags"):
        build_competitor_discovery_candidate(
            workflow_run_id="run_1", direction_id="competitor_discovery", claim_type="named_competitor",
            competitor_name="竞品A", fact=_fact(field_path), context=CONTEXT,
        )


def test_competitor_factory_rejects_a_name_not_in_its_quote():
    with pytest.raises(ValueError, match="must occur in its source quote"):
        build_competitor_discovery_candidate(
            workflow_run_id="run_1", direction_id="competitor_discovery", claim_type="named_competitor",
            competitor_name="竞品A", fact=_fact(text="通勤场景使用"), context=CONTEXT,
        )


@pytest.mark.parametrize("text", ["竞品A 是官方账号", "竞品A 市场领导", "竞品A 竞争表现最好"])
def test_competitor_factory_rejects_identity_market_and_performance_claims(text):
    with pytest.raises(ValueError, match="cannot claim identity, market status, or performance"):
        build_competitor_discovery_candidate(
            workflow_run_id="run_1", direction_id="competitor_discovery", claim_type="named_competitor",
            competitor_name="竞品A", fact=_fact(text=text), context=CONTEXT,
        )


def test_evaluator_rejects_an_author_only_competitor_candidate():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1"
    )
    contract = next(item for item in contracts if item.direction_id == "competitor_discovery")
    policy = next(item for item in policies if item.direction_id == "competitor_discovery")
    packet = DirectionalEvidencePacketRecord(
        "dep_1", "v1", {"field_availability": {field: "present" for field in contract.required_note_fields}},
        workflow_run_id="run_1", research_direction_id="competitor_discovery", canonical_source_id="cs_1", field_projection_hash="packet-hash",
    )
    candidate = ClaimCandidateRecord(
        "cc_author", "v2", {"scope": {"sample": "selected_packets", "competitor_name": "竞品A", "author": "作者甲", "canonical_source_id": "cs_1", "engagement_context": CONTEXT["engagement_context"]}, "quote_refs": [{"field_path": "author"}]},
        workflow_run_id="run_1", research_direction_id="competitor_discovery", evidence_packet_id="dep_1",
        statement="竞品A", intent_id="competitor_identification", claim_type="named_competitor",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate, packet=packet, contract=contract, sample_policy=policy,
        policy_snapshot=snapshot, selected_source_count=10, independent_author_count=10,
    ).record

    assert competitor_discovery_boundary_reason(candidate) == "competitor_discovery_evidence_boundary_violation"
    assert result.decision == "rejected"


def test_missing_competitor_blocking_fields_still_downgrade_a_valid_candidate():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1"
    )
    contract = next(item for item in contracts if item.direction_id == "competitor_discovery")
    policy = next(item for item in policies if item.direction_id == "competitor_discovery")
    candidate = build_competitor_discovery_candidate(
        workflow_run_id="run_1", direction_id="competitor_discovery", claim_type="named_competitor",
        competitor_name="竞品A", fact=_fact(), context=CONTEXT,
    )
    packet = DirectionalEvidencePacketRecord(
        "dep_1", "v1", {"field_availability": {}}, workflow_run_id="run_1",
        research_direction_id="competitor_discovery", canonical_source_id="cs_1", field_projection_hash="packet-hash",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate, packet=packet, contract=contract, sample_policy=policy,
        policy_snapshot=snapshot, selected_source_count=0, independent_author_count=0,
    ).record

    assert result.decision == "downgraded"
    assert "blocking_field_missing" in result.payload["reason_codes"]
