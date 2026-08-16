from __future__ import annotations

import pytest

from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def _contract(*, version: int):
    return build_scope_contract(
        workflow_run_id="run_scope_1",
        research_plan_id="rp_scope_1",
        version=version,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "must_cover", "required"),
            ScopeConstraint("season", "季节", "夏季", "must_cover", "required"),
            ScopeConstraint("scenario", "使用场景", "通勤", "must_cover", "required"),
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
