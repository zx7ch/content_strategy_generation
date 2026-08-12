from __future__ import annotations

from datetime import datetime, timezone

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
from app.content_research.workflow.directional_pipeline import compile_query_groups

ENGAGEMENT_CONTEXT = {"metrics": {"like_count": 120}, "metrics_observed_at": "2026-07-18T00:00:00+00:00"}


def _fact(field_path: str = "content_text", text: str = "通勤场景的清单式开头") -> ExtractedFact:
    return ExtractedFact("run_1", "content_performance", "dep_1", field_path, text, "https://example/note")


def _snapshot():
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    groups = compile_query_groups(
        direction_id="content_performance",
        subject="通勤",
        questions=["内容"],
        competitors=[],
        run_as_of_at=frozen_at,
    )
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1",
        workflow_run_id="run_1",
        brief_id="rb_1",
        plan_id="rp_1",
        run_as_of_at=frozen_at,
        direction_ids=("content_performance",),
        confirmed_subject="通勤",
        query_groups_by_direction={
            "content_performance": tuple(
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
    snapshot, policies, contracts, groups = _snapshot()
    contract = next(item for item in contracts if item.direction_id == "content_performance")
    policy = next(item for item in policies if item.direction_id == "content_performance")
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {
            "field_projection": {
                "content_text": _fact().text,
                "source_url": _fact().source_url,
                "author_id": "author-1",
            },
            "field_availability": {
                field: "present" for field in contract.required_note_fields
            },
            "retrieval_context": {
                "query_group_ids": [groups[0].id],
                "query_hits": [{"query_group_id": groups[0].id, "rank": 1}],
                "query_plan_hash": snapshot.effective_policy["locked_query_plan"][
                    "directions"
                ]["content_performance"]["query_plan_hash"],
            },
        },
        workflow_run_id="run_1",
        research_direction_id="content_performance",
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )
    candidate = ClaimCandidateRecord(
        "cc_metric", "v2", {"scope": {"sample": "selected_packets", "engagement_context": ENGAGEMENT_CONTEXT}, "quote_refs": [{"field_path": "metrics"}]},
        workflow_run_id="run_1", research_direction_id="content_performance", evidence_packet_id="dep_1",
        statement="点赞 120", intent_id="engagement_cohort", claim_type="observed_high_engagement_sample",
    )

    assert content_performance_boundary_reason(candidate) == "content_performance_evidence_boundary_violation"
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


def test_missing_snapshot_fields_still_downgrade_an_otherwise_valid_candidate():
    snapshot, policies, contracts, groups = _snapshot()
    contract = next(item for item in contracts if item.direction_id == "content_performance")
    policy = next(item for item in policies if item.direction_id == "content_performance")
    fact = _fact()
    candidate = build_content_performance_candidate(
        workflow_run_id="run_1", direction_id="content_performance", claim_type="visible_content_format",
        fact=fact, engagement_context=ENGAGEMENT_CONTEXT,
    )
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {
            "field_projection": {
                "content_text": fact.text,
                "source_url": fact.source_url,
                "author_id": "author-1",
            },
            "field_availability": {},
            "retrieval_context": {
                "query_group_ids": [groups[0].id],
                "query_hits": [{"query_group_id": groups[0].id, "rank": 1}],
                "query_plan_hash": snapshot.effective_policy["locked_query_plan"][
                    "directions"
                ]["content_performance"]["query_plan_hash"],
            },
        },
        workflow_run_id="run_1",
        research_direction_id="content_performance",
        canonical_source_id="cs_1",
        field_projection_hash="packet-hash",
    )

    result = ClaimAdmissionEvaluator().evaluate(
        candidate=candidate, packet=packet, contract=contract, sample_policy=policy,
        policy_snapshot=snapshot, selected_source_count=0, relevance_qualified_source_count=0, eligible_source_count=0, independent_author_count=0,
        admission_packet_identities=(("dep_1", "packet-hash"),),
    ).record

    assert result.decision == "downgraded"
    assert "blocking_field_missing" in result.payload["reason_codes"]
