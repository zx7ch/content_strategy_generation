from __future__ import annotations

import pytest

from app.content_research.scope_contract import (
    CoverageSnapshot,
    ResearchScopeContract,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
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
