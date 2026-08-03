from __future__ import annotations

from datetime import datetime, timezone

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
from app.content_research.workflow.directional_pipeline import compile_query_groups


def _fact(field_path: str = "content_text", text: str = "通勤时轻便好收纳") -> ExtractedFact:
    return ExtractedFact("run_1", "product_marketing", "dep_1", field_path, text, "https://example/note")


def _frozen_snapshot(*, snapshot_id: str = "rps_relevance"):
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    groups = compile_query_groups(
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
        run_as_of_at=frozen_at,
    )
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id=snapshot_id,
        workflow_run_id="run_1",
        brief_id="rb_1",
        plan_id="rp_1",
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        confirmed_subject="速干徒步短裤",
        query_groups_by_direction={
            "product_marketing": tuple(
                {
                    "id": group.id,
                    "direction_id": group.direction_id,
                    "normalized_query": group.query,
                    "priority": group.priority,
                    "sort": group.sort,
                    "time_window": dict(group.time_window or {}),
                    "candidate_cap": group.candidate_limit,
                }
                for group in groups
            )
        },
    )
    return snapshot, policies, contracts, groups


def _packet_for_fact(
    *,
    fact: ExtractedFact,
    snapshot,
    groups,
    availability,
    packet_id: str | None = None,
    author_id: str = "author-1",
    retrieval_context: dict | None = None,
):
    direction = snapshot.effective_policy["locked_query_plan"]["directions"][
        "product_marketing"
    ]
    return DirectionalEvidencePacketRecord(
        packet_id or fact.evidence_packet_id,
        "v1",
        {
            "field_projection": {
                fact.field_path: fact.text,
                "source_url": fact.source_url,
                "author_id": author_id,
            },
            "field_availability": availability,
            "retrieval_context": retrieval_context
            or {
                "query_group_ids": [groups[0].id],
                "query_hits": [{"query_group_id": groups[0].id, "rank": 1}],
                "query_plan_hash": direction["query_plan_hash"],
            },
        },
        workflow_run_id=fact.workflow_run_id,
        research_direction_id=fact.direction_id,
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )


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
    snapshot, policies, contracts, groups = _frozen_snapshot()
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    availability = {field: "present" for field in contract.required_note_fields}
    packet = _packet_for_fact(
        fact=_fact("content_text", "短裤点赞更高"),
        snapshot=snapshot,
        groups=groups,
        availability=availability,
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

    assert product_marketing_boundary_reason(candidate) == "product_marketing_evidence_boundary_violation"
    with pytest.raises(
        ValueError, match="claim candidate quote reference does not match packet"
    ):
        ClaimAdmissionEvaluator().evaluate(
            candidate=candidate,
            packet=packet,
            contract=contract,
            sample_policy=policy,
            policy_snapshot=snapshot,
            selected_source_count=10,
            relevance_qualified_source_count=10,
            eligible_source_count=10,
            independent_author_count=10,
            admission_packet_identities=(("dep_1", "packet-hash"),),
        )


def test_missing_product_marketing_fields_still_downgrade_an_otherwise_valid_candidate():
    snapshot, policies, contracts, groups = _frozen_snapshot()
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    fact = _fact(text="短裤通勤时轻便好收纳")
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="product_value_expression",
        fact=fact,
    )
    packet = _packet_for_fact(
        fact=fact,
        snapshot=snapshot,
        groups=groups,
        availability={},
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=0,
        relevance_qualified_source_count=0,
        eligible_source_count=0,
        independent_author_count=0,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "downgraded"
    assert "blocking_field_missing" in result.payload["reason_codes"]


def test_evaluator_rejects_unrelated_query_subject_quote_even_with_metrics_and_query_provenance():
    snapshot, policies, contracts, groups = _frozen_snapshot()
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    availability = {field: "present" for field in contract.required_note_fields}
    fact = _fact("title", "打工人必学的向上管理黑话")
    packet = _packet_for_fact(
        fact=fact,
        snapshot=snapshot,
        groups=groups,
        availability=availability,
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=fact,
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=3,
        relevance_qualified_source_count=3,
        eligible_source_count=3,
        independent_author_count=2,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "rejected"
    assert result.payload["reason_codes"] == ["query_subject_not_supported"]


def test_evaluator_admits_title_backed_message_angle_matching_frozen_category_anchor():
    snapshot, policies, contracts, groups = _frozen_snapshot()
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    frozen = contract.metadata["query_relevance"]
    assert frozen["subject_anchors"] == ["速干徒步短裤"]
    assert frozen["category_anchors"] == ["短裤"]
    assert frozen["matching_mode"] == "normalized_substring_any_anchor_v1"
    assert frozen["reason_code"] == "query_subject_not_supported"
    assert snapshot.effective_policy["query_relevance"]["product_marketing"] == frozen
    fact = _fact("title", "夏日短裤怎么穿：轻量徒步搭配")
    packet = _packet_for_fact(
        fact=fact,
        snapshot=snapshot,
        groups=groups,
        availability={
            field: "present" for field in contract.required_note_fields
        },
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=fact,
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=3,
        relevance_qualified_source_count=3,
        eligible_source_count=3,
        independent_author_count=2,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "admitted"
    assert result.payload["reason_codes"] == []


def test_admission_records_selected_relevant_eligible_and_stable_author_metrics():
    snapshot, policies, contracts, groups = _frozen_snapshot(
        snapshot_id="rps_metrics"
    )
    contract = contracts[0]
    fact = ExtractedFact(
        "run_1",
        "product_marketing",
        "dep_metrics",
        "title",
        "短裤夏日轻量搭配",
        "https://example/note",
    )
    packet = _packet_for_fact(
        fact=fact,
        snapshot=snapshot,
        groups=groups,
        availability={
            field: "present" for field in contract.required_note_fields
        },
        packet_id="dep_metrics",
        author_id="author-stable",
    )
    packet = DirectionalEvidencePacketRecord(
        packet.id,
        packet.schema_version,
        packet.payload,
        workflow_run_id=packet.workflow_run_id,
        research_direction_id=packet.research_direction_id,
        canonical_source_id=packet.canonical_source_id,
        field_projection_hash="packet-metrics",
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=fact,
    )

    decision = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policies[0],
        policy_snapshot=snapshot,
        selected_source_count=4,
        relevance_qualified_source_count=3,
        eligible_source_count=2,
        independent_author_count=2,
        admission_packet_identities=(("dep_metrics", "packet-metrics"),),
    ).record

    assert decision.payload["computed_metrics"] == {
        "selected_source_count": 4,
        "relevance_qualified_source_count": 3,
        "eligible_source_count": 2,
        "independent_author_count": 2,
            "author_id": "author-stable",
            "author_identity_kind": "id",
            "missing_required_fields": [],
    }
    assert decision.decision == "downgraded"
    assert decision.payload["reason_codes"] == ["sample_threshold_unmet"]


def test_evaluator_rejects_anchor_matching_quote_without_frozen_query_group_provenance():
    snapshot, policies, contracts, groups = _frozen_snapshot()
    contract = contracts[0]
    policy = policies[0]
    fact = _fact("title", "夏日短裤怎么穿")
    packet = _packet_for_fact(
        fact=fact,
        snapshot=snapshot,
        groups=groups,
        availability={
            field: "present" for field in contract.required_note_fields
        },
        retrieval_context={"query_group_ids": []},
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=fact,
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=3,
        relevance_qualified_source_count=3,
        eligible_source_count=3,
        independent_author_count=2,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "rejected"
    assert result.payload["reason_codes"] == ["query_subject_not_supported"]


def test_evaluator_fails_closed_when_a_legacy_snapshot_has_no_frozen_relevance_contract():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_legacy",
        workflow_run_id="run_1",
        brief_id="rb_1",
        plan_id="rp_1",
        direction_ids=("product_marketing",),
    )
    contract = contracts[0]
    policy = policies[0]
    fact = _fact("title", "夏日短裤怎么穿")
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {
            "field_projection": {
                "title": fact.text,
                "source_url": fact.source_url,
                "author_id": "author-1",
            },
            "field_availability": {
                field: "present" for field in contract.required_note_fields
            },
            "retrieval_context": {"query_group_ids": ["qg_legacy"]},
        },
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=fact,
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contract,
        sample_policy=policy,
        policy_snapshot=snapshot,
        selected_source_count=3,
        relevance_qualified_source_count=3,
        eligible_source_count=3,
        independent_author_count=2,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "rejected"
    assert result.payload["reason_codes"] == ["query_subject_not_supported"]


def test_evaluator_rejects_a_fabricated_quote_that_does_not_belong_to_the_packet():
    snapshot, policies, contracts, groups = _frozen_snapshot()
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {
            "field_projection": {
                "title": "短裤真实标题",
                "source_url": "https://example/note",
                "author_id": "author-1",
            },
            "field_availability": {
                field: "present"
                for field in contracts[0].required_note_fields
            },
            "retrieval_context": {
                "query_group_ids": [groups[0].id],
                "query_hits": [{"query_group_id": groups[0].id, "rank": 1}],
                "query_plan_hash": snapshot.effective_policy["locked_query_plan"][
                    "directions"
                ]["product_marketing"]["query_plan_hash"],
            },
        },
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=_fact("title", "短裤伪造标题"),
    )

    with pytest.raises(
        ValueError, match="claim candidate quote reference does not match packet"
    ):
        ClaimAdmissionEvaluator().evaluate(
            candidate=candidate,
            packet=packet,
            contract=contracts[0],
            sample_policy=policies[0],
            policy_snapshot=snapshot,
            selected_source_count=3,
            relevance_qualified_source_count=3,
            eligible_source_count=3,
            independent_author_count=2,
            admission_packet_identities=(("dep_1", "packet-hash"),),
        )


def test_evaluator_rejects_id_only_query_provenance_from_an_otherwise_current_packet():
    snapshot, policies, contracts, groups = _frozen_snapshot()
    text = "夏日短裤怎么穿"
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {
            "field_projection": {
                "title": text,
                "source_url": "https://example/note",
                "author_id": "author-1",
            },
            "field_availability": {
                field: "present"
                for field in contracts[0].required_note_fields
            },
            "retrieval_context": {"query_group_ids": [groups[0].id]},
        },
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        claim_type="message_angle",
        fact=_fact("title", text),
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate,
        packet=packet,
        contract=contracts[0],
        sample_policy=policies[0],
        policy_snapshot=snapshot,
        selected_source_count=3,
        relevance_qualified_source_count=3,
        eligible_source_count=3,
        independent_author_count=2,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "rejected"
    assert result.payload["reason_codes"] == ["query_subject_not_supported"]
