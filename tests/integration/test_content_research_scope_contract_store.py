from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace

import pytest

from app.content_research.execution_decision_identity import build_execution_decision_identity
from app.content_research.models import ResearchBriefRecord
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ResearchScopeContract,
    ResearchScopeDraft,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeDraftAuditEvent,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeQueryGroupInput,
    build_scope_contract,
    build_scope_draft,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def _contract(*, version: int) -> ResearchScopeContract:
    return build_scope_contract(
        workflow_run_id="run_scope_1",
        research_plan_id="rp_scope_1",
        version=version,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
            ScopeConstraint("season", "季节", "夏季", "required"),
            ScopeConstraint("scenario", "使用场景", "通勤", "required"),
        ),
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )


def _confirmation_rows(db_path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        return list(
            conn.execute(
                """SELECT scope_draft_id, scope_contract_id, workflow_run_id
                   FROM content_research_scope_draft_confirmations
                   ORDER BY scope_draft_id"""
            )
        )


def _confirm_scope(
    store: SQLiteContentResearchStore,
    *,
    draft: ResearchScopeDraft,
    event_id: str,
    final_queries: tuple[str, ...] | None = None,
) -> tuple[ResearchScopeContract, ScopeAuditEvent, bool]:
    return store.confirm_scope_atomically(
        draft.id,
        final_queries=final_queries or tuple(group.final_query for group in draft.query_groups),
        event_id=event_id,
    )


def _save_current_brief(
    store: SQLiteContentResearchStore, *, workflow_run_id: str, structure_hash: str
) -> None:
    store.save_brief(
        ResearchBriefRecord(
            id=f"brief_{workflow_run_id}",
            workflow_run_id=workflow_run_id,
            thread_id="thread_scope",
            schema_version="content_research_brief_v1",
            status="ready",
            payload={
                "schema_version": "content_research_brief_v1",
                "subject_structure_hash": structure_hash,
            },
        )
    )


def test_scope_contract_versions_and_scope_audit_events_are_append_only(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-contract.db"))
    v1 = _contract(version=1)
    v2 = _contract(version=2)

    store.save_scope_contract(v1)
    store.save_scope_contract(v2)
    event = ScopeAuditEvent(
        id="sca_1",
        workflow_run_id=v1.workflow_run_id,
        scope_contract_id=v1.id,
        scope_contract_version=v1.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1", "query_group_count": 1},
    )
    store.append_scope_audit_event(event)
    snapshot = CoverageSnapshot(
        id="scv_1",
        workflow_run_id=v1.workflow_run_id,
        scope_contract_id=v1.id,
        scope_contract_version=v1.version,
        state="awaiting_scope_decision",
        constraint_counts={"season": {"matched": 5, "required": True}},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)

    assert store.get_scope_contract(v1.workflow_run_id, version=1) == v1
    assert store.list_scope_contracts(v1.workflow_run_id) == [v1, v2]
    assert store.list_scope_audit_events(v1.workflow_run_id, version=1) == [event]
    assert store.get_coverage_snapshot(v1.workflow_run_id, version=1) == snapshot
    with pytest.raises(ValueError, match="append-only"):
        store.append_scope_audit_event(event)


def test_competing_atomic_coverage_resolutions_reconcile_same_and_reject_different(
    tmp_path,
) -> None:
    def run_race(db_name: str, resolutions: tuple[str, str]):
        db_path = tmp_path / db_name
        stores = [
            SQLiteContentResearchStore(str(db_path)),
            SQLiteContentResearchStore(str(db_path)),
        ]
        contract = _contract(version=1)
        stores[0].save_scope_contract(contract)
        snapshot = CoverageSnapshot(
            id=f"scv_{db_name}",
            workflow_run_id=contract.workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=contract.version,
            state="awaiting_scope_decision",
            constraint_counts={},
            unmet_constraint_ids=("season",),
        )
        stores[0].save_coverage_snapshot(snapshot)
        barrier = threading.Barrier(2)
        results = []
        errors: list[BaseException] = []

        def resolve(store, resolution):
            operation = (
                "limited_report"
                if resolution == "generate_limited_report"
                else "supplementary_collection"
            )
            authorization = ScopeExecutionAuthorization(
                id=f"sea_{resolution}",
                workflow_run_id=contract.workflow_run_id,
                scope_contract_id=contract.id,
                scope_contract_version=contract.version,
                coverage_snapshot_id=snapshot.id,
                resolution=resolution,
                execution_revision=2,
                state=(
                    "authorized_limited_report"
                    if resolution == "generate_limited_report"
                    else "authorized_collection"
                ),
            )
            continuation = ScopeExecutionContinuation(
                id=f"sec_{resolution}",
                authorization_id=authorization.id,
                workflow_run_id=contract.workflow_run_id,
                execution_revision=2,
                operation=operation,
                supplementary_queries=(
                    ("夏季 防晒 长袖衬衫",) if resolution == "expand_required_constraint" else ()
                ),
                state="pending",
            )
            event = ScopeAuditEvent(
                id=f"sae_{resolution}",
                workflow_run_id=contract.workflow_run_id,
                scope_contract_id=contract.id,
                scope_contract_version=contract.version,
                event_name="coverage_resolved",
                    payload={
                        "schema_version": "content_research_scope_audit_event_v1",
                        "coverage_snapshot_id": snapshot.id,
                        "resolution": resolution,
                        "constraint_id": (
                            "season"
                            if resolution == "expand_required_constraint"
                            else ""
                        ),
                    },
            )
            try:
                barrier.wait()
                results.append(
                    store.resolve_coverage_and_authorize_execution_atomically(
                        snapshot=snapshot,
                        authorization=authorization,
                        continuation=continuation,
                        event=event,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=resolve, args=(store, resolution))
            for store, resolution in zip(stores, resolutions, strict=True)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        return stores[0], results, errors

    same_store, same_results, same_errors = run_race(
        "same.db",
        ("generate_limited_report", "generate_limited_report"),
    )
    assert same_errors == []
    assert len(same_results) == 2
    assert sorted(result[4] for result in same_results) == [False, True]
    assert len(same_store.list_scope_execution_authorizations("run_scope_1")) == 1
    assert len(same_store.list_scope_execution_continuations("run_scope_1")) == 1

    different_store, different_results, different_errors = run_race(
        "different.db",
        ("generate_limited_report", "expand_required_constraint"),
    )
    assert len(different_results) == 1
    assert len(different_errors) == 1
    assert isinstance(different_errors[0], ValueError)
    assert "coverage_decision_already_resolved" in str(different_errors[0])
    assert len(different_store.list_scope_execution_authorizations("run_scope_1")) == 1
    assert len(different_store.list_scope_execution_continuations("run_scope_1")) == 1


def test_coverage_decision_replay_uses_one_execution_unit_and_one_decision_fact(tmp_path) -> None:
    """Removing decision-unit idempotency must make this exact replay create a new unit."""
    store = SQLiteContentResearchStore(str(tmp_path / "execution-unit.db"))
    contract = _contract(version=1)
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_execution_unit",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)
    decision = {
        "resolution": "expand_required_constraint",
        "operation": "supplementary_collection",
        "constraint_id": "season",
        "supplementary_queries": ("夏季 防晒 长袖衬衫",),
    }

    unit, created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot, decision=decision
    )
    replayed, replay_created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot, decision=decision
    )

    assert unit.id == replayed.id
    assert created is True
    assert replay_created is False
    assert [fact.kind for fact in store.execution_trace(unit.id)] == ["decision_accepted"]
    expected = build_execution_decision_identity(
        coverage_snapshot_id=snapshot.id,
        source_scope_contract_id=contract.id,
        resulting_scope_contract_id=contract.id,
        resolution="expand_required_constraint",
        target_constraint_id="season",
        supplementary_queries=("夏季 防晒 长袖衬衫",),
    )
    with sqlite3.connect(store._db_path) as conn:
        identity_schema, identity_json, identity_state = conn.execute(
            "SELECT identity_schema, identity_json, identity_state "
            "FROM content_research_scope_execution_units WHERE id=?",
            (unit.id,),
        ).fetchone()
    assert identity_schema == "execution_decision_identity_v1"
    assert identity_json == expected.canonical_json
    assert identity_state == "canonical"
    assert unit.identity_json == expected.canonical_json
    assert unit.recovery_state == "replayable"


def test_competing_execution_unit_decisions_reconcile_exact_replays_and_reject_conflicts(
    tmp_path,
) -> None:
    """Changing a decision for one coverage snapshot must not create another executable unit."""
    db_path = tmp_path / "execution-unit-race.db"
    stores = [SQLiteContentResearchStore(str(db_path)) for _ in range(2)]
    contract = _contract(version=1)
    stores[0].save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_execution_unit_race",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    stores[0].save_coverage_snapshot(snapshot)
    decision = {"resolution": "generate_limited_report", "operation": "limited_report"}
    barrier = threading.Barrier(2)
    results = []

    def resolve(store: SQLiteContentResearchStore) -> None:
        barrier.wait()
        results.append(
            store.resolve_coverage_to_execution_unit_atomically(
                snapshot=snapshot, decision=decision
            )
        )

    threads = [threading.Thread(target=resolve, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert len({item[0].id for item in results}) == 1
    assert sorted(item[1] for item in results) == [False, True]

    with pytest.raises(ValueError, match="coverage_decision_already_resolved"):
        stores[0].resolve_coverage_to_execution_unit_atomically(
            snapshot=snapshot,
            decision={
                "resolution": "expand_required_constraint",
                "operation": "supplementary_collection",
                "constraint_id": "season",
                "supplementary_queries": ("夏季 长袖衬衫",),
            },
        )


@pytest.mark.parametrize(
    ("first_decision", "conflicting_decision"),
    [
        (
            {
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ("夏季 长袖衬衫",),
            },
            {
                "resolution": "expand_required_constraint",
                "constraint_id": "scenario",
                "supplementary_queries": ("夏季 长袖衬衫",),
            },
        ),
        (
            {
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ("夏季 长袖衬衫",),
            },
            {
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ("夏季 防晒 长袖衬衫",),
            },
        ),
    ],
)
def test_execution_decision_compatibility_matrix_replays_exact_identity_and_rejects_conflicts(
    tmp_path, first_decision, conflicting_decision
) -> None:
    """Target and normalized query changes must be visible without permitting a second decision."""
    store = SQLiteContentResearchStore(str(tmp_path / "execution-unit-matrix.db"))
    contract = _contract(version=1)
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_execution_unit_matrix",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season", "scenario"),
    )
    store.save_coverage_snapshot(snapshot)

    unit, created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot, decision=first_decision
    )
    replay, replay_created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot,
        decision={
            **first_decision,
            "supplementary_queries": tuple(
                f"  {query.replace(' ', '   ')}  "
                for query in first_decision["supplementary_queries"]
            ),
        },
    )

    assert created is True
    assert replay_created is False
    assert replay.id == unit.id
    with pytest.raises(ValueError, match="coverage_decision_already_resolved"):
        store.resolve_coverage_to_execution_unit_atomically(
            snapshot=snapshot, decision=conflicting_decision
        )


def test_execution_trace_sequences_are_transition_owned_and_contiguous(tmp_path) -> None:
    """Allowing callers to choose a fact sequence must make this audit trace forgeable."""
    store = SQLiteContentResearchStore(str(tmp_path / "execution-fact-sequence.db"))
    contract = _contract(version=1)
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_execution_fact_sequence",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)
    unit, _ = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot,
        decision={"resolution": "generate_limited_report", "operation": "limited_report"},
    )

    with pytest.raises(RuntimeError, match="allocated by execution-unit transitions"):
        store.append_execution_fact(
            execution_unit_id=unit.id,
            attempt_no=0,
            sequence_no=99,
            kind="attempt_claimed",
            payload={"owner": "worker-a"},
        )
    claim = store.claim_execution_unit(execution_unit_id=unit.id, owner="worker-a")
    assert claim is not None
    assert store.record_provider_request(
        execution_unit_id=unit.id,
        attempt_no=claim.attempt_no,
        lease_token=str(claim.lease_token),
        payload={"request": "provider"},
    )
    facts = store.execution_trace(unit.id)
    assert [fact.sequence_no for fact in facts] == [1, 2, 3]
    with pytest.raises(TypeError):
        facts[-1].payload["forged"] = True  # type: ignore[index]


def test_legacy_authorization_and_execution_unit_resolvers_share_exact_replay_identity(
    tmp_path,
) -> None:
    """Adding a legacy-only value to an identity must fail this cross-seam replay."""
    store = SQLiteContentResearchStore(str(tmp_path / "execution-unit-alias.db"))
    contract = _contract(version=1)
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_execution_unit_alias",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)
    authorization = ScopeExecutionAuthorization(
        id="sea_execution_unit_alias",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        coverage_snapshot_id=snapshot.id,
        resolution="expand_required_constraint",
        execution_revision=2,
        state="authorized_collection",
    )
    continuation = ScopeExecutionContinuation(
        id="sec_execution_unit_alias",
        authorization_id=authorization.id,
        workflow_run_id=contract.workflow_run_id,
        execution_revision=2,
        operation="supplementary_collection",
        supplementary_queries=("夏季 防晒 长袖衬衫",),
        state="pending",
    )
    event = ScopeAuditEvent(
        id="sae_execution_unit_alias",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="coverage_resolved",
        payload={
            "schema_version": "content_research_scope_audit_event_v1",
            "coverage_snapshot_id": snapshot.id,
            "resolution": "expand_required_constraint",
            "constraint_id": "season",
        },
    )
    _, _, persisted_authorization, _, _ = store.resolve_coverage_and_authorize_execution_atomically(
        snapshot=snapshot,
        authorization=authorization,
        continuation=continuation,
        event=event,
    )

    normalized_replay = store.resolve_coverage_and_authorize_execution_atomically(
        snapshot=snapshot,
        authorization=replace(authorization, id="sea_execution_unit_alias_replay"),
        continuation=replace(
            continuation,
            id="sec_execution_unit_alias_replay",
            authorization_id="sea_execution_unit_alias_replay",
            supplementary_queries=("  夏季   防晒 长袖衬衫  ",),
        ),
        event=event,
    )
    assert normalized_replay[4] is False

    unit, created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot,
        decision={
            "resolution": "expand_required_constraint",
            "operation": "supplementary_collection",
            "constraint_id": "season",
            "supplementary_queries": ("夏季 防晒 长袖衬衫",),
        },
    )

    assert created is False
    assert unit.id == persisted_authorization.execution_unit_id


def test_expired_lease_is_reclaimed_and_stale_token_cannot_write_or_complete(tmp_path) -> None:
    """Removing the live-expiry predicate must make stale A mutate B's unit."""
    store = SQLiteContentResearchStore(str(tmp_path / "execution-unit-lease.db"))
    contract = _contract(version=1)
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_execution_unit_lease",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)
    unit, _ = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot,
        decision={"resolution": "generate_limited_report", "operation": "limited_report"},
    )
    claim_a = store.claim_execution_unit(execution_unit_id=unit.id, owner="A", lease_seconds=0)
    assert claim_a is not None
    claim_b = store.claim_execution_unit(execution_unit_id=unit.id, owner="B", lease_seconds=120)
    assert claim_b is not None
    assert claim_b.attempt_no == claim_a.attempt_no + 1
    assert not store.record_provider_request(
        execution_unit_id=unit.id,
        attempt_no=claim_a.attempt_no,
        lease_token=str(claim_a.lease_token),
        payload={"request": "stale"},
    )
    assert not store.complete_execution_unit(
        execution_unit_id=unit.id,
        attempt_no=claim_a.attempt_no,
        owner="A",
        lease_token=str(claim_a.lease_token),
        state="completed",
    )
    assert store.complete_execution_unit(
        execution_unit_id=unit.id,
        attempt_no=claim_b.attempt_no,
        owner="B",
        lease_token=str(claim_b.lease_token),
        state="outcome_unknown",
    )
    assert [fact.kind for fact in store.execution_trace(unit.id)][-1] == "outcome_unknown"


def test_scope_draft_and_suggestion_audit_event_commit_atomically(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-draft.db"))
    draft = build_scope_draft(
        workflow_run_id="run_scope_1",
        research_plan_id="rp_scope_1",
        structure_hash="structure_hash_1",
        constraints=_contract(version=1).constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    event = ScopeDraftAuditEvent(
        id="sda_1",
        workflow_run_id=draft.workflow_run_id,
        scope_draft_id=draft.id,
        event_name="scope_suggested",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )

    store.save_scope_draft_with_audit_event(draft, event)

    assert store.get_scope_draft(draft.id) == draft
    with pytest.raises(ValueError, match="append-only"):
        store.save_scope_draft_with_audit_event(draft, event)


def test_scope_records_reject_a_reference_to_another_or_missing_contract(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-contract.db"))
    contract = _contract(version=1)
    store.save_scope_contract(contract)

    with pytest.raises(ValueError, match="does not match a persisted scope contract"):
        store.append_scope_audit_event(
            ScopeAuditEvent(
                id="sca_missing",
                workflow_run_id=contract.workflow_run_id,
                scope_contract_id="rsc_missing",
                scope_contract_version=1,
                event_name="scope_confirmed",
                payload={"schema_version": "content_research_scope_audit_event_v1"},
            )
        )

    with pytest.raises(ValueError, match="does not match a persisted scope contract"):
        store.save_coverage_snapshot(
            CoverageSnapshot(
                id="scv_mismatch",
                workflow_run_id="other_run",
                scope_contract_id=contract.id,
                scope_contract_version=1,
                state="satisfied",
                constraint_counts={},
                unmet_constraint_ids=(),
            )
        )


def test_contract_and_confirmation_event_commit_atomically(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-contract.db"))
    contract = _contract(version=1)
    event = ScopeAuditEvent(
        id="sca_confirmed",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )

    store.save_scope_contract_with_audit_event(contract, event)

    assert store.get_scope_contract(contract.workflow_run_id, version=1) == contract
    assert store.list_scope_audit_events(contract.workflow_run_id, version=1) == [event]


def test_contract_rolls_back_when_atomic_confirmation_event_insert_fails(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-contract.db"))
    contract = _contract(version=1)
    existing_contract = _contract(version=2)
    store.save_scope_contract(existing_contract)
    store.append_scope_audit_event(
        ScopeAuditEvent(
            id="sca_duplicate",
            workflow_run_id=existing_contract.workflow_run_id,
            scope_contract_id=existing_contract.id,
            scope_contract_version=existing_contract.version,
            event_name="scope_confirmed",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        )
    )
    duplicate_event = ScopeAuditEvent(
        id="sca_duplicate",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )

    with pytest.raises(ValueError, match="append-only"):
        store.save_scope_contract_with_audit_event(contract, duplicate_event)

    assert store.get_scope_contract(contract.workflow_run_id, version=1) is None


def test_scope_draft_confirmation_is_idempotent_and_persists_a_single_contract(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-confirmation.db"))
    contract_v1 = _contract(version=1)
    draft = build_scope_draft(
        workflow_run_id=contract_v1.workflow_run_id,
        research_plan_id=contract_v1.research_plan_id,
        structure_hash="structure_hash_1",
        constraints=contract_v1.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="sda_confirmed",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    _save_current_brief(
        store, workflow_run_id=draft.workflow_run_id, structure_hash=draft.structure_hash
    )
    event_v1 = ScopeAuditEvent(
        id="sca_confirmed_v1",
        workflow_run_id=contract_v1.workflow_run_id,
        scope_contract_id=contract_v1.id,
        scope_contract_version=contract_v1.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )

    first, first_event, created = _confirm_scope(store, draft=draft, event_id=event_v1.id)
    second, repeated_event, repeated = _confirm_scope(
        store, draft=draft, event_id="sca_confirmed_repeated"
    )

    assert created is True
    assert repeated is False
    assert second == first
    assert repeated_event == first_event
    assert store.list_scope_contracts(draft.workflow_run_id) == [first]
    assert store.list_scope_audit_events(draft.workflow_run_id, version=1) == [first_event]


def test_scope_draft_confirmation_replay_rejects_a_stale_current_brief(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-confirmation-stale-replay.db"))
    contract = _contract(version=1)
    draft = build_scope_draft(
        workflow_run_id=contract.workflow_run_id,
        research_plan_id=contract.research_plan_id,
        structure_hash="structure_hash_1",
        constraints=contract.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="sda_stale_replay",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    _save_current_brief(
        store, workflow_run_id=draft.workflow_run_id, structure_hash=draft.structure_hash
    )
    first, event, created = _confirm_scope(store, draft=draft, event_id="sca_stale_replay")
    assert created is True

    _save_current_brief(
        store, workflow_run_id=draft.workflow_run_id, structure_hash="structure_hash_2"
    )

    with pytest.raises(ValueError, match="current brief"):
        _confirm_scope(store, draft=draft, event_id="sca_stale_replay_again")

    assert store.list_scope_contracts(draft.workflow_run_id) == [first]
    assert store.list_scope_audit_events(draft.workflow_run_id, version=1) == [event]


def test_unresolved_coverage_atomically_blocks_new_draft_and_confirmation(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-decision-exclusion.db"))
    template = _contract(version=1)
    first_draft = build_scope_draft(
        workflow_run_id=template.workflow_run_id,
        research_plan_id=template.research_plan_id,
        structure_hash="structure_hash_exclusion",
        constraints=template.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    pending_draft = build_scope_draft(
        workflow_run_id=template.workflow_run_id,
        research_plan_id=template.research_plan_id,
        structure_hash="structure_hash_exclusion",
        constraints=template.constraints,
        query_groups=(ScopeQueryGroupInput("长袖衬衫 通勤", "长袖衬衫 通勤"),),
    )
    for draft, event_id in (
        (first_draft, "sda_exclusion_first"),
        (pending_draft, "sda_exclusion_pending"),
    ):
        store.save_scope_draft_with_audit_event(
            draft,
            ScopeDraftAuditEvent(
                id=event_id,
                workflow_run_id=draft.workflow_run_id,
                scope_draft_id=draft.id,
                event_name="scope_suggested",
                payload={"schema_version": "content_research_scope_audit_event_v1"},
            ),
        )
    _save_current_brief(
        store,
        workflow_run_id=template.workflow_run_id,
        structure_hash=first_draft.structure_hash,
    )
    contract, _, _ = _confirm_scope(
        store, draft=first_draft, event_id="sca_exclusion_first"
    )
    store.save_coverage_snapshot(
        CoverageSnapshot(
            id="scv_exclusion",
            workflow_run_id=contract.workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=contract.version,
            state="awaiting_scope_decision",
            constraint_counts={},
            unmet_constraint_ids=("season",),
        )
    )

    with pytest.raises(ValueError, match="coverage_decision_required"):
        _confirm_scope(store, draft=pending_draft, event_id="sca_exclusion_pending")

    later_draft = replace(
        pending_draft,
        id="rsd_exclusion_later",
    )
    with pytest.raises(ValueError, match="coverage_decision_required"):
        store.save_scope_draft_with_audit_event(
            later_draft,
            ScopeDraftAuditEvent(
                id="sda_exclusion_later",
                workflow_run_id=later_draft.workflow_run_id,
                scope_draft_id=later_draft.id,
                event_name="scope_suggested",
                payload={"schema_version": "content_research_scope_audit_event_v1"},
            ),
        )

    assert store.list_scope_contracts(contract.workflow_run_id) == [contract]


def test_conflicting_scope_draft_repeat_does_not_create_a_second_contract(tmp_path) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "scope-confirmation-conflict.db"))
    contract_v1 = _contract(version=1)
    contract_v2 = _contract(version=2)
    draft = build_scope_draft(
        workflow_run_id=contract_v1.workflow_run_id,
        research_plan_id=contract_v1.research_plan_id,
        structure_hash="structure_hash_1",
        constraints=contract_v1.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="sda_conflict",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    _save_current_brief(
        store, workflow_run_id=draft.workflow_run_id, structure_hash=draft.structure_hash
    )
    event_v1 = ScopeAuditEvent(
        id="sca_conflict_v1",
        workflow_run_id=contract_v1.workflow_run_id,
        scope_contract_id=contract_v1.id,
        scope_contract_version=contract_v1.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )
    event_v2 = ScopeAuditEvent(
        id="sca_conflict_v2",
        workflow_run_id=contract_v2.workflow_run_id,
        scope_contract_id=contract_v2.id,
        scope_contract_version=contract_v2.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )

    first, first_event, created = _confirm_scope(store, draft=draft, event_id=event_v1.id)
    repeated, repeated_event, created_again = _confirm_scope(
        store, draft=draft, event_id=event_v2.id
    )

    assert created is True
    assert created_again is False
    assert repeated == first
    assert repeated_event == first_event
    assert store.list_scope_contracts(draft.workflow_run_id) == [first]
    assert store.get_scope_contract(draft.workflow_run_id, version=2) is None


def test_scope_draft_confirmation_constructs_confirmation_audit_event(tmp_path) -> None:
    db_path = tmp_path / "scope-confirmation-invalid-event.db"
    store = SQLiteContentResearchStore(str(db_path))
    contract = _contract(version=1)
    draft = build_scope_draft(
        workflow_run_id=contract.workflow_run_id,
        research_plan_id=contract.research_plan_id,
        structure_hash="structure_hash_1",
        constraints=contract.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="sda_invalid_event",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    _save_current_brief(
        store, workflow_run_id=draft.workflow_run_id, structure_hash=draft.structure_hash
    )
    confirmed, event, created = _confirm_scope(
        store,
        draft=draft,
        event_id="sca_confirmed_by_store",
    )

    assert created is True
    assert event.event_name == "scope_confirmed"
    assert event.scope_contract_id == confirmed.id
    assert event.payload["scope_draft_id"] == draft.id
    assert store.list_scope_contracts(draft.workflow_run_id) == [confirmed]
    assert store.list_scope_audit_events(draft.workflow_run_id, version=1) == [event]
    assert _confirmation_rows(db_path) == [(draft.id, confirmed.id, draft.workflow_run_id)]


def test_scope_draft_confirmation_rolls_back_contract_audit_and_link_on_late_failure(
    tmp_path,
) -> None:
    db_path = tmp_path / "scope-confirmation-rollback.db"
    store = SQLiteContentResearchStore(str(db_path))
    contract = _contract(version=1)
    draft = build_scope_draft(
        workflow_run_id=contract.workflow_run_id,
        research_plan_id=contract.research_plan_id,
        structure_hash="structure_hash_1",
        constraints=contract.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="sda_rollback",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    _save_current_brief(
        store, workflow_run_id=draft.workflow_run_id, structure_hash=draft.structure_hash
    )
    event = ScopeAuditEvent(
        id="sca_rollback",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO content_research_scope_draft_confirmations
               (scope_draft_id, scope_contract_id, workflow_run_id, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                "rsd_link_already_taken",
                contract.id,
                contract.workflow_run_id,
                "2026-08-17T00:00:00+00:00",
            ),
        )

    with pytest.raises(sqlite3.IntegrityError):
        _confirm_scope(store, draft=draft, event_id=event.id)

    assert store.list_scope_contracts(draft.workflow_run_id) == []
    assert store.list_scope_audit_events(draft.workflow_run_id, version=1) == []
    assert _confirmation_rows(db_path) == [
        ("rsd_link_already_taken", contract.id, contract.workflow_run_id)
    ]


def test_two_connections_racing_to_confirm_one_draft_create_one_contract(tmp_path) -> None:
    db_path = tmp_path / "scope-confirmation-race.db"
    first_store = SQLiteContentResearchStore(str(db_path))
    second_store = SQLiteContentResearchStore(str(db_path))
    contract = _contract(version=1)
    draft = build_scope_draft(
        workflow_run_id=contract.workflow_run_id,
        research_plan_id=contract.research_plan_id,
        structure_hash="structure_hash_1",
        constraints=contract.constraints,
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    first_store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="sda_race",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    _save_current_brief(
        first_store,
        workflow_run_id=draft.workflow_run_id,
        structure_hash=draft.structure_hash,
    )
    event = ScopeAuditEvent(
        id="sca_race",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )
    barrier = threading.Barrier(2)
    results: list[tuple[ResearchScopeContract, ScopeAuditEvent, bool]] = []
    errors: list[BaseException] = []

    def confirm(store: SQLiteContentResearchStore) -> None:
        try:
            barrier.wait()
            results.append(_confirm_scope(store, draft=draft, event_id=event.id))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=confirm, args=(first_store,))
    second = threading.Thread(target=confirm, args=(second_store,))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert sorted(created for _, _, created in results) == [False, True]
    confirmed_contracts = [confirmed for confirmed, _, _ in results]
    assert confirmed_contracts == [confirmed_contracts[0], confirmed_contracts[0]]
    confirmed_events = [confirmed_event for _, confirmed_event, _ in results]
    assert confirmed_events == [confirmed_events[0], confirmed_events[0]]
    assert first_store.list_scope_contracts(draft.workflow_run_id) == [confirmed_contracts[0]]
    assert first_store.list_scope_audit_events(draft.workflow_run_id, version=1) == [
        confirmed_events[0]
    ]
    assert _confirmation_rows(db_path) == [(draft.id, contract.id, contract.workflow_run_id)]


def test_two_connections_confirming_distinct_drafts_allocate_distinct_versions(tmp_path) -> None:
    db_path = tmp_path / "scope-confirmation-distinct-drafts-race.db"
    first_store = SQLiteContentResearchStore(str(db_path))
    second_store = SQLiteContentResearchStore(str(db_path))
    base_contract = _contract(version=1)
    drafts = [
        build_scope_draft(
            workflow_run_id=base_contract.workflow_run_id,
            research_plan_id=f"rp_scope_{index}",
            structure_hash="structure_hash_shared",
            constraints=base_contract.constraints,
            query_groups=(
                ScopeQueryGroupInput(
                    "夏季 长袖衬衫 通勤",
                    "夏季 长袖衬衫 通勤",
                    ("夏季", "长袖衬衫", "通勤"),
                ),
            ),
        )
        for index in (1, 2)
    ]
    for index, draft in enumerate(drafts, start=1):
        first_store.save_scope_draft_with_audit_event(
            draft,
            ScopeDraftAuditEvent(
                id=f"sda_distinct_{index}",
                workflow_run_id=draft.workflow_run_id,
                scope_draft_id=draft.id,
                event_name="scope_suggested",
                payload={"schema_version": "content_research_scope_audit_event_v1"},
            ),
        )
    _save_current_brief(
        first_store,
        workflow_run_id=base_contract.workflow_run_id,
        structure_hash="structure_hash_shared",
    )
    barrier = threading.Barrier(2)
    results: list[tuple[ResearchScopeContract, ScopeAuditEvent, bool]] = []
    errors: list[BaseException] = []

    def confirm(
        store: SQLiteContentResearchStore,
        draft: ResearchScopeDraft,
        event_id: str,
    ) -> None:
        try:
            barrier.wait()
            results.append(
                store.confirm_scope_atomically(
                    draft.id,
                    final_queries=("夏季 长袖衬衫 通勤",),
                    event_id=event_id,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=confirm, args=(first_store, drafts[0], "sca_distinct_1"))
    second = threading.Thread(target=confirm, args=(second_store, drafts[1], "sca_distinct_2"))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert sorted(contract.version for contract, _, _ in results) == [1, 2]
    assert len({contract.id for contract, _, _ in results}) == 2
    assert all(created for _, _, created in results)
    persisted = first_store.list_scope_contracts(base_contract.workflow_run_id)
    assert [contract.version for contract in persisted] == [1, 2]
    assert {contract.id for contract in persisted} == {contract.id for contract, _, _ in results}
    assert len(_confirmation_rows(db_path)) == 2
