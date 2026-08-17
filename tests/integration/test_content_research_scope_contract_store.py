from __future__ import annotations

import sqlite3
import threading

import pytest

from app.content_research.scope_contract import (
    CoverageSnapshot,
    ResearchScopeContract,
    ResearchScopeDraft,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeDraftAuditEvent,
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
        query_groups=(
            ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),
        ),
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
        final_queries=final_queries
        or tuple(group.final_query for group in draft.query_groups),
        event_id=event_id,
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
    event_v1 = ScopeAuditEvent(
        id="sca_confirmed_v1",
        workflow_run_id=contract_v1.workflow_run_id,
        scope_contract_id=contract_v1.id,
        scope_contract_version=contract_v1.version,
        event_name="scope_confirmed",
        payload={"schema_version": "content_research_scope_audit_event_v1"},
    )

    first, first_event, created = _confirm_scope(
        store, draft=draft, event_id=event_v1.id
    )
    second, repeated_event, repeated = _confirm_scope(
        store, draft=draft, event_id="sca_confirmed_repeated"
    )

    assert created is True
    assert repeated is False
    assert second == first
    assert repeated_event == first_event
    assert store.list_scope_contracts(draft.workflow_run_id) == [first]
    assert store.list_scope_audit_events(draft.workflow_run_id, version=1) == [first_event]


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

    first, first_event, created = _confirm_scope(
        store, draft=draft, event_id=event_v1.id
    )
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
    assert _confirmation_rows(db_path) == [
        (draft.id, confirmed.id, draft.workflow_run_id)
    ]


def test_scope_draft_confirmation_rolls_back_contract_audit_and_link_on_late_failure(tmp_path) -> None:
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
            ("rsd_link_already_taken", contract.id, contract.workflow_run_id, "2026-08-17T00:00:00+00:00"),
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
    assert first_store.list_scope_contracts(draft.workflow_run_id) == [
        confirmed_contracts[0]
    ]
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
            structure_hash=f"structure_hash_{index}",
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

    first = threading.Thread(
        target=confirm, args=(first_store, drafts[0], "sca_distinct_1")
    )
    second = threading.Thread(
        target=confirm, args=(second_store, drafts[1], "sca_distinct_2")
    )
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
    assert {contract.id for contract in persisted} == {
        contract.id for contract, _, _ in results
    }
    assert len(_confirmation_rows(db_path)) == 2
