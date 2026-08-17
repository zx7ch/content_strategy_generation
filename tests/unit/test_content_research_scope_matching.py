from __future__ import annotations

from datetime import datetime, timezone

from app.content_research.admission.candidates import ExtractedFact
from app.content_research.admission.product_marketing import (
    build_product_marketing_candidate,
)
from app.content_research.admission.relevance import (
    evaluate_scope_match,
    query_relevance_reason,
)
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import DirectionalEvidencePacketRecord
from app.content_research.scope_contract import (
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.workflow.directional_pipeline import (
    build_packet,
    compile_query_groups,
)


def _summer_commute_contract():
    return build_scope_contract(
        workflow_run_id="run_scope_matching",
        research_plan_id="rp_scope_matching",
        version=1,
        constraints=(
            ScopeConstraint(
                "core_object",
                "核心对象",
                "长袖衬衫",
                "required",
                ("衬衫",),
            ),
            ScopeConstraint("season", "季节", "夏季", "required"),
            ScopeConstraint("scenario", "使用场景", "通勤", "required"),
        ),
        query_groups=(
            ScopeQueryGroupInput(
                "夏季 长袖衬衫 通勤",
                "夏季 长袖衬衫 通勤",
                ("长袖衬衫", "夏季", "通勤"),
            ),
        ),
    )


def test_core_alias_and_required_contexts_admit_a_candidate() -> None:
    contract = _summer_commute_contract()

    match = evaluate_scope_match(
        source={
            "canonical_source_id": "note_summer",
            "title": "夏季通勤衬衫",
            "content_text": "轻薄不易皱",
            "tags": [],
            "source_metadata": {"author_id": "author_summer"},
            "retrieval_context": {
                "query_group_ids": [contract.query_groups[0].id],
            },
        },
        contract=contract,
    )

    assert match.constraint_matches["core_object"].status == "matched"
    assert match.constraint_matches["core_object"].evidence == ("衬衫",)
    assert match.constraint_matches["season"].status == "matched"
    assert match.constraint_matches["season"].evidence == ("夏季",)
    assert match.constraint_matches["scenario"].status == "matched"
    assert match.constraint_matches["scenario"].evidence == ("通勤",)
    assert match.query_group_hits == (contract.query_groups[0].id,)
    assert match.scope_contract_version == 1
    assert match.eligibility == "eligible"
    assert match.exclusion_reasons == ()


def test_missing_required_summer_excludes_an_autumn_only_candidate() -> None:
    contract = _summer_commute_contract()

    match = evaluate_scope_match(
        source={
            "canonical_source_id": "note_autumn",
            "title": "秋季通勤衬衫",
            "content_text": "适合早秋办公室",
            "tags": ["通勤", "衬衫"],
            "source_metadata": {"season": "秋季", "author_id": "author_autumn"},
            "retrieval_context": {
                "query_group_ids": [contract.query_groups[0].id],
            },
        },
        contract=contract,
    )

    assert match.constraint_matches["season"].status == "unmatched"
    assert match.eligibility == "excluded"
    assert match.exclusion_reasons == ("required_constraint_unmatched:season",)


def test_scope_match_uses_source_metadata_from_the_persisted_packet() -> None:
    contract = _summer_commute_contract()
    packet = build_packet(
        direction_id="product_marketing",
        canonical_source_id="note_metadata",
        fields={
            "title": "通勤衬衫",
            "content_text": "轻薄不易皱",
            "tags": [],
            "source_metadata": {
                "season": "夏季",
                "cookie": "session-secret",
                "nested": {
                    "safe_label": "commute",
                    "access_token": "provider-secret",
                },
            },
        },
        availability={},
        retrieval_context={"query_group_ids": [contract.query_groups[0].id]},
    )

    match = evaluate_scope_match(
        source={
            **packet["field_projection"],
            "retrieval_context": packet["retrieval_context"],
        },
        contract=contract,
    )

    assert match.constraint_matches["season"].evidence_fields == (
        "source_metadata.season",
    )
    assert match.eligibility == "eligible"
    assert packet["field_projection"]["source_metadata"] == {
        "season": "夏季",
        "nested": {"safe_label": "commute"},
    }


def test_scope_provenance_rejects_a_packet_with_a_forged_query_plan_hash() -> None:
    scope_contract = _summer_commute_contract()
    frozen_at = datetime(2026, 8, 17, tzinfo=timezone.utc)
    legacy_groups = compile_query_groups(
        direction_id="product_marketing",
        subject="夏季通勤长袖",
        questions=["卖点"],
        competitors=[],
        run_as_of_at=frozen_at,
    )
    snapshot, _policies, contracts = build_default_snapshot(
        snapshot_id="rps_scope_provenance",
        workflow_run_id=scope_contract.workflow_run_id,
        brief_id="rb_scope_provenance",
        plan_id=scope_contract.research_plan_id,
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        confirmed_subject="夏季通勤长袖",
        subject_structure={
            "core_entities": [{"canonical_name": "长袖衬衫"}],
            "research_intents": ["通勤"],
        },
        subject_structure_hash="scope-provenance-structure-hash",
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
                for group in legacy_groups
            )
        },
    )
    direction_contract = contracts[0]
    fact = ExtractedFact(
        scope_contract.workflow_run_id,
        "product_marketing",
        "dep_scope_provenance",
        "content_text",
        "夏季通勤衬衫轻薄不易皱",
        "https://example.test/note",
    )
    candidate = build_product_marketing_candidate(
        workflow_run_id=scope_contract.workflow_run_id,
        direction_id="product_marketing",
        claim_type="product_value_expression",
        fact=fact,
    )
    packet = DirectionalEvidencePacketRecord(
        "dep_scope_provenance",
        "content_research_directional_packet_v1",
        {
            "field_projection": {
                "content_text": fact.text,
                "source_url": fact.source_url,
            },
            "field_availability": {},
            "retrieval_context": {
                "query_group_ids": [scope_contract.query_groups[0].id],
                "query_hits": [
                    {"query_group_id": scope_contract.query_groups[0].id, "rank": 1}
                ],
                "query_plan_hash": "forged-plan-hash",
            },
        },
        workflow_run_id=scope_contract.workflow_run_id,
        research_direction_id="product_marketing",
        canonical_source_id="note_scope_provenance",
        field_projection_hash="packet-hash",
    )

    assert (
        query_relevance_reason(
            candidate=candidate,
            packet=packet,
            contract=direction_contract,
            policy_snapshot=snapshot,
            scope_contract=scope_contract,
            scope_query_plan_hash="expected-plan-hash",
        )
        == "invalid_query_provenance"
    )
