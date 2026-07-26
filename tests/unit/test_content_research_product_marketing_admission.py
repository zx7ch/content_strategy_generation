from __future__ import annotations

import pytest

from app.content_research.admission.candidates import ExtractedFact
from app.content_research.admission.evaluator import ClaimAdmissionEvaluator
from app.content_research.admission.product_marketing import (
    PRODUCT_MARKETING_CLAIM_INTENTS,
    build_product_marketing_candidate,
    product_marketing_boundary_reason,
)
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)


def _fact(field_path: str = "content_text", text: str = "通勤时轻便好收纳") -> ExtractedFact:
    return ExtractedFact("run_1", "product_marketing", "dep_1", field_path, text, "https://example/note")


@pytest.mark.parametrize("claim_type,intent", PRODUCT_MARKETING_CLAIM_INTENTS.items())
def test_product_marketing_factory_maps_each_allowed_claim_to_its_intent(claim_type, intent):
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type=claim_type,
        fact=_fact("title" if claim_type == "message_angle" else "content_text"),
    )

    assert candidate.claim_type == claim_type
    assert candidate.intent_id == intent
    assert candidate.payload["scope"]["sample"] == "selected_packets"


@pytest.mark.parametrize(
    "claim_type,field_path",
    [
        ("product_value_expression", "title"),
        ("use_context", "title"),
        ("target_audience_framing", "title"),
        ("message_angle", "metrics"),
    ],
)
def test_product_marketing_factory_rejects_unallowed_evidence_fields(claim_type, field_path):
    with pytest.raises(ValueError, match="cannot use this evidence field"):
        build_product_marketing_candidate(
            workflow_run_id="run_1",
            direction_id="product_marketing",
            claim_type=claim_type,
            fact=_fact(field_path),
        )


@pytest.mark.parametrize("text", ["用户偏好这个表达", "转化效果提升", "购买更多", "存在因果关系"])
def test_product_marketing_factory_rejects_preference_conversion_and_causal_outcomes(text):
    with pytest.raises(ValueError, match="cannot claim preference, conversion, or effect"):
        build_product_marketing_candidate(
            workflow_run_id="run_1",
            direction_id="product_marketing",
            claim_type="product_value_expression",
            fact=_fact(text=text),
        )


def test_product_marketing_factory_uses_a_bounded_first_verbatim_observation_with_source_offsets():
    text = "通勤时轻便好收纳\n" + "后续无关内容" * 100
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="product_value_expression",
        fact=_fact(text=text),
    )

    quote = candidate.payload["quote_refs"][0]
    assert candidate.statement == "通勤时轻便好收纳"
    assert quote["quote"] == candidate.statement
    assert quote["text_start"] == 0
    assert quote["text_end"] == len(candidate.statement)


def test_product_marketing_boundary_rejects_an_unbounded_or_non_verbatim_claim():
    candidate = ClaimCandidateRecord(
        "cc_unbounded",
        "v2",
        {
            "scope": {"sample": "selected_packets"},
            "quote_refs": [{"field_path": "content_text", "quote": "x" * 281}],
        },
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        evidence_packet_id="dep_1",
        statement="x" * 281,
        intent_id="value_proposition",
        claim_type="product_value_expression",
    )

    assert product_marketing_boundary_reason(candidate) == "product_marketing_evidence_boundary_violation"


def test_evaluator_rejects_a_metric_sourced_product_marketing_candidate():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1"
    )
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    availability = {field: "present" for field in contract.required_note_fields}
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {"field_availability": availability},
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )
    candidate = ClaimCandidateRecord(
        "cc_metric",
        "v2",
        {"scope": {"sample": "selected_packets"}, "quote_refs": [{"field_path": "metrics"}]},
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        evidence_packet_id="dep_1",
        statement="点赞更高",
        intent_id="value_proposition",
        claim_type="product_value_expression",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=10,
        independent_author_count=10,
    ).record

    assert product_marketing_boundary_reason(candidate) == "product_marketing_evidence_boundary_violation"
    assert result.decision == "rejected"
    assert "product_marketing_evidence_boundary_violation" in result.payload["reason_codes"]


def test_missing_product_marketing_fields_still_downgrade_an_otherwise_valid_candidate():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1"
    )
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="product_value_expression",
        fact=_fact(),
    )
    packet = DirectionalEvidencePacketRecord(
        "dep_1", "v1", {"field_availability": {}}, workflow_run_id="run_1",
        research_direction_id="product_marketing", canonical_source_id="cs_1", field_projection_hash="packet-hash",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=0,
        independent_author_count=0,
    ).record

    assert result.decision == "downgraded"
    assert "blocking_field_missing" in result.payload["reason_codes"]
