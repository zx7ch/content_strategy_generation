from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import app.content_research.admission.evaluator as evaluator_module
import app.content_research.contracts as contracts_module
from app.content_research.admission.candidates import (
    ExtractedFact,
    build_claim_candidate,
)
from app.content_research.admission.evaluator import ClaimAdmissionEvaluator
from app.content_research.contracts import build_default_snapshot, policy_hash
from app.content_research.persistence_models import DirectionalEvidencePacketRecord
from app.content_research.workflow.directional_pipeline import compile_query_groups


def _inputs(*, claim_type="product_value_expression", available=True):
    frozen_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    groups = compile_query_groups(
        direction_id="product_marketing",
        subject="样本",
        questions=["表达"],
        competitors=[],
        run_as_of_at=frozen_at,
    )
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_1",
        workflow_run_id="run_1",
        brief_id="rb_1",
        plan_id="rp_1",
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        confirmed_subject="样本",
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
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    availability = {field: "present" for field in contract.required_note_fields} if available else {}
    packet = DirectionalEvidencePacketRecord(
        "dep_1",
        "v1",
        {
            "field_projection": {
                "content_text": "样本表达",
                "source_url": "https://example/note",
                "author_id": "author-1",
            },
            "field_availability": availability,
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
    candidate = build_claim_candidate(
        workflow_run_id="run_1",
        direction_id="product_marketing",
        intent_id="value_proposition",
        claim_type=claim_type,
        statement="样本表达",
        scope={"sample": "selected_packets"},
        fact=ExtractedFact(
            "run_1",
            "product_marketing",
            "dep_1",
            "content_text",
            "样本表达",
            "https://example/note",
        ),
        quote="样本表达",
        text_start=0,
        text_end=4,
    )
    return candidate, packet, contract, next(item for item in policies if item.direction_id == "product_marketing"), snapshot


def test_admission_is_deterministic_and_records_recomputed_metrics():
    result = ClaimAdmissionEvaluator().evaluate(candidate=_inputs()[0], packet=_inputs()[1], contract=_inputs()[2], sample_policy=_inputs()[3], policy_snapshot=_inputs()[4], selected_source_count=10, relevance_qualified_source_count=10, eligible_source_count=10, independent_author_count=10, admission_packet_identities=(("dep_1", "packet-hash"),))
    assert result.record.decision == "admitted"
    assert result.record.payload["claim_evidence_state"] == "repeated_observation"
    assert result.record.payload["decision_fingerprint"] == result.fingerprint


def test_admission_rejects_prohibited_type_and_downgrades_missing_evidence():
    candidate, packet, contract, policy, snapshot = _inputs(claim_type="causal_claim")
    assert ClaimAdmissionEvaluator().evaluate(candidate=candidate, packet=packet, contract=contract, sample_policy=policy, policy_snapshot=snapshot, selected_source_count=1, relevance_qualified_source_count=1, eligible_source_count=1, independent_author_count=1, admission_packet_identities=(("dep_1", "packet-hash"),)).record.decision == "rejected"
    candidate, packet, contract, policy, snapshot = _inputs(available=False)
    result = ClaimAdmissionEvaluator().evaluate(candidate=candidate, packet=packet, contract=contract, sample_policy=policy, policy_snapshot=snapshot, selected_source_count=0, relevance_qualified_source_count=0, eligible_source_count=0, independent_author_count=0, admission_packet_identities=(("dep_1", "packet-hash"),))
    assert result.record.decision == "downgraded"
    assert "blocking_field_missing" in result.record.payload["reason_codes"]


def test_decision_fingerprint_changes_for_every_replay_sensitive_input():
    candidate, packet, contract, policy, snapshot = _inputs()
    evaluator = ClaimAdmissionEvaluator()
    common = {
        "candidate": candidate,
        "packet": packet,
        "contract": contract,
        "sample_policy": policy,
        "policy_snapshot": snapshot,
        "selected_source_count": 3,
        "relevance_qualified_source_count": 3,
        "eligible_source_count": 3,
        "independent_author_count": 2,
        "admission_packet_identities": (("dep_1", "packet-hash"),),
    }
    baseline = evaluator.evaluate(**common).fingerprint
    variants = [
        {**common, "policy_snapshot": replace(snapshot, id="rps_2")},
        {
            **common,
            "admission_packet_identities": (
                ("dep_1", "packet-hash"),
                ("dep_2", "other-packet-hash"),
            ),
        },
        {**common, "sample_policy": replace(policy, minimum_samples=4)},
        {
            **common,
            "sample_policy": replace(policy, minimum_independent_authors=3),
        },
        {**common, "sample_policy": replace(policy, author_cap=4)},
        {
            **common,
            "sample_policy": replace(
                policy, metadata={**policy.metadata, "detail_fetch_cap": 31}
            ),
        },
        {**common, "selected_source_count": 4},
        {**common, "relevance_qualified_source_count": 2},
        {**common, "eligible_source_count": 2},
        {**common, "independent_author_count": 1},
    ]

    assert all(evaluator.evaluate(**variant).fingerprint != baseline for variant in variants)


def test_decision_fingerprint_changes_with_admission_and_relevance_versions(
    monkeypatch,
):
    candidate, packet, contract, policy, snapshot = _inputs()
    common = {
        "candidate": candidate,
        "packet": packet,
        "contract": contract,
        "sample_policy": policy,
        "policy_snapshot": snapshot,
        "selected_source_count": 3,
        "relevance_qualified_source_count": 3,
        "eligible_source_count": 3,
        "independent_author_count": 2,
        "admission_packet_identities": (("dep_1", "packet-hash"),),
    }
    baseline = ClaimAdmissionEvaluator().evaluate(**common).fingerprint

    with monkeypatch.context() as patch:
        patch.setattr(
            evaluator_module, "ALGORITHM_VERSION", "claim_admission_v_next"
        )
        next_policy = deepcopy(snapshot.effective_policy)
        next_policy["admission_algorithm_version"] = "claim_admission_v_next"
        next_snapshot = replace(
            snapshot,
            effective_policy=next_policy,
            effective_policy_hash=policy_hash(next_policy),
        )
        admission_version_fingerprint = ClaimAdmissionEvaluator().evaluate(
            **{**common, "policy_snapshot": next_snapshot}
        ).fingerprint

    with monkeypatch.context() as patch:
        patch.setattr(
            contracts_module,
            "QUERY_RELEVANCE_ALGORITHM_VERSION",
            "query_relevance_v_next",
        )
        next_relevance = deepcopy(contract.metadata["query_relevance"])
        next_relevance["algorithm_version"] = "query_relevance_v_next"
        next_contract = replace(
            contract, metadata={"query_relevance": next_relevance}
        )
        next_policy = deepcopy(snapshot.effective_policy)
        next_policy["query_relevance"]["product_marketing"] = next_relevance
        next_snapshot = replace(
            snapshot,
            effective_policy=next_policy,
            effective_policy_hash=policy_hash(next_policy),
        )
        relevance_version_fingerprint = ClaimAdmissionEvaluator().evaluate(
            **{
                **common,
                "contract": next_contract,
                "policy_snapshot": next_snapshot,
            }
        ).fingerprint

    assert admission_version_fingerprint != baseline
    assert relevance_version_fingerprint != baseline
