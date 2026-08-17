from __future__ import annotations

import pytest

from app.content_research.admission.candidates import source_text_hash
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow.directional_pipeline import (
    persist_scope_coverage_evaluation,
)


def test_scope_coverage_persists_exact_counts_reasons_and_corresponding_audits(
    tmp_path,
) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-coverage.db"))
    contract = build_scope_contract(
        workflow_run_id="run_scope_coverage",
        research_plan_id="rp_scope_coverage",
        version=1,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required", ("衬衫",)),
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
    store.save_scope_contract(contract)
    query_group_id = contract.query_groups[0].id

    snapshot = persist_scope_coverage_evaluation(
        store=store,
        contract=contract,
        candidates=(
            {
                "canonical_source_id": "note_summer",
                "title": "夏季通勤衬衫",
                "content_text": "轻薄不易皱",
                "tags": [],
                "author_id": "author_summer",
                "retrieval_context": {"query_group_ids": [query_group_id]},
            },
            {
                "canonical_source_id": "note_autumn",
                "title": "秋季通勤衬衫",
                "content_text": "适合早秋办公室",
                "tags": ["通勤", "衬衫"],
                "author_id": "author_autumn",
                "retrieval_context": {"query_group_ids": [query_group_id]},
            },
        ),
        query_group_outcomes={
            query_group_id: {
                "status": "completed",
                "discovered_count": 2,
                "failure_code": None,
            }
        },
        minimum_samples=2,
        minimum_independent_authors=2,
    )

    persisted = store.get_coverage_snapshot(contract.workflow_run_id, version=1)
    assert persisted == snapshot
    assert snapshot.state == "awaiting_scope_decision"
    assert snapshot.unmet_constraint_ids == ("season",)
    assert snapshot.constraint_counts == {
        "core_object": {
            "matched_candidate_count": 2,
            "independent_author_count": 2,
            "required": True,
        },
        "season": {
            "matched_candidate_count": 1,
            "independent_author_count": 1,
            "required": True,
        },
        "scenario": {
            "matched_candidate_count": 2,
            "independent_author_count": 2,
            "required": True,
        },
        "_query_groups": {
            query_group_id: {
                "candidate_count": 2,
                "eligible_candidate_count": 1,
                "independent_author_count": 2,
            }
        },
        "_summary": {
            "candidate_count": 2,
            "eligible_candidate_count": 1,
            "independent_author_count": 1,
            "minimum_samples": 2,
            "minimum_independent_authors": 2,
            "reason_codes": [
                "required_constraint_coverage_unmet:season",
                "minimum_eligible_scope_samples_unmet",
                "minimum_independent_authors_unmet",
            ],
        },
    }

    events = store.list_scope_audit_events(contract.workflow_run_id, version=1)
    assert [event.event_name for event in events] == [
        "query_group_collected",
        "candidate_scope_evaluated",
        "candidate_scope_evaluated",
        "coverage_evaluated",
    ]
    query_event = events[0].payload
    assert query_event["query_group_id"] == query_group_id
    assert query_event["final_query"] == contract.query_groups[0].final_query
    assert query_event["discovered_count"] == 2

    candidate_events = {event.payload["candidate_id"]: event.payload for event in events[1:3]}
    assert candidate_events["note_summer"]["eligibility"] == "eligible"
    assert candidate_events["note_autumn"]["exclusion_reasons"] == [
        "required_constraint_unmatched:season"
    ]
    assert candidate_events["note_autumn"]["constraint_matches"]["season"] == {
        "status": "unmatched",
        "evidence": [],
        "evidence_fields": [],
    }

    coverage_event = events[-1].payload
    assert coverage_event["coverage_snapshot_id"] == snapshot.id
    assert coverage_event["state"] == snapshot.state
    assert coverage_event["constraint_counts"] == snapshot.constraint_counts
    assert coverage_event["unmet_constraint_ids"] == list(snapshot.unmet_constraint_ids)
    assert coverage_event["reason_codes"] == snapshot.constraint_counts["_summary"][
        "reason_codes"
    ]


def test_coverage_snapshot_rolls_back_when_its_audit_cannot_commit(
    tmp_path, monkeypatch
) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-coverage-atomic.db"))
    contract = build_scope_contract(
        workflow_run_id="run_scope_atomic",
        research_plan_id="rp_scope_atomic",
        version=1,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "衬衫", "required"),
        ),
        query_groups=(ScopeQueryGroupInput("衬衫", "衬衫", ("衬衫",)),),
    )
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_atomic",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="satisfied",
        constraint_counts={},
        unmet_constraint_ids=(),
    )
    event = ScopeAuditEvent(
        id="sae_atomic",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="coverage_evaluated",
        payload={
            "schema_version": "content_research_scope_audit_event_v1",
            "coverage_snapshot_id": snapshot.id,
        },
    )

    def fail_audit_insert(_conn, _event) -> None:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(store, "_insert_scope_audit_event", fail_audit_insert)

    with pytest.raises(RuntimeError, match="audit insert failed"):
        store.save_coverage_snapshot_with_audit_event(snapshot, event)

    assert store.get_coverage_snapshot(contract.workflow_run_id, version=1) is None
    assert store.list_scope_audit_events(contract.workflow_run_id, version=1) == []


def test_claim_scope_projection_rolls_back_when_its_audit_cannot_commit(
    tmp_path, monkeypatch
) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-candidate-atomic.db"))
    contract = build_scope_contract(
        workflow_run_id="run_scope_candidate_atomic",
        research_plan_id="rp_scope_candidate_atomic",
        version=1,
        constraints=(ScopeConstraint("core_object", "核心对象", "衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("衬衫", "衬衫", ("衬衫",)),),
    )
    store.save_scope_contract(contract)
    source = CanonicalSourceRecord(
        "src_scope_atomic",
        "v1",
        {},
        platform="xhs",
        platform_source_kind="note",
        platform_source_id="note_scope_atomic",
    )
    store.save_canonical_source(source)
    packet = DirectionalEvidencePacketRecord(
        "dep_scope_atomic",
        "v1",
        {
            "field_projection": {
                "content_text": "衬衫",
                "source_url": "https://example.test/scope-atomic",
            },
            "field_availability": {},
            "retrieval_context": {},
        },
        workflow_run_id=contract.workflow_run_id,
        research_direction_id="product_marketing",
        canonical_source_id=source.id,
        field_projection_hash="scope-atomic-packet-hash",
    )
    store.save_directional_evidence_packet(packet)
    candidate = ClaimCandidateRecord(
        "cc_scope_atomic",
        "v2",
        {
            "quote_refs": [
                {
                    "field_path": "content_text",
                    "quote": "衬衫",
                    "text_start": 0,
                    "text_end": 2,
                    "source_text_hash": source_text_hash("衬衫"),
                    "source_url": "https://example.test/scope-atomic",
                }
            ],
            "scope_match": {"scope_contract_version": contract.version},
        },
        workflow_run_id=contract.workflow_run_id,
        research_direction_id="product_marketing",
        evidence_packet_id=packet.id,
        statement="衬衫",
        intent_id="product_value_expression",
        claim_type="observation",
    )
    event = ScopeAuditEvent(
        id="sae_scope_candidate_atomic",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="candidate_scope_evaluated",
        payload={
            "schema_version": "content_research_scope_audit_event_v1",
            "candidate_id": source.id,
            "claim_candidate_id": candidate.id,
        },
    )

    def fail_audit_insert(_conn, _event) -> None:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(store, "_insert_scope_audit_event", fail_audit_insert)

    with pytest.raises(RuntimeError, match="audit insert failed"):
        store.save_claim_candidate_with_scope_audit_event(candidate, event)

    assert store.get_typed_record(ClaimCandidateRecord, candidate.id) is None
    assert store.list_scope_audit_events(contract.workflow_run_id, version=1) == []
