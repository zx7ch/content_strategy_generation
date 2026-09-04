from __future__ import annotations

from pathlib import Path

import pytest

from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.models import ResearchBriefRecord
from app.content_research.scope_contract import (
    CoverageSnapshot,
    DispatchLeaseContext,
    ExecutionContext,
    ExecutionLeaseFencedError,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.stores.mutations import content_research_store_handlers
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections
from app.runtime_write_handlers import production_runtime_write_handlers


@pytest.mark.asyncio
async def test_content_research_store_records_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "content-research-store.sqlite"
    SQLiteContentResearchStore(str(database))
    opened: list[SQLiteConnectionOpened] = []

    with observe_sqlite_connections(opened.append):
        writer = RuntimeWriteCoordinator(database, handlers=content_research_store_handlers())
        await writer.start()
        store = SQLiteContentResearchStore(str(database), writer=writer)
        brief = ResearchBriefRecord(
            id="brief-owned",
            workflow_run_id="run-owned",
            thread_id="thread-owned",
            schema_version="content_research_brief_v1",
            status="draft",
            payload={"schema_version": "content_research_brief_v1", "subject": "通勤穿搭"},
        )

        assert store.save_brief(brief) == brief
        assert store.get_brief(brief.id) == brief
        await writer.close()

    assert len([event for event in opened if event.role == "writer"]) == 1
    assert {event.role for event in opened} == {"writer", "reader"}


@pytest.mark.asyncio
async def test_dispatch_scoped_reader_uses_readonly_connection_and_preserves_lease_fence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dispatch-scoped-reader.sqlite"
    SQLiteContentResearchStore(str(database))
    writer = RuntimeWriteCoordinator(
        database,
        handlers=production_runtime_write_handlers(),
    )
    await writer.start()
    try:
        dispatch = AsyncFormalResearchDispatchRepository(str(database), writer=writer)
        await dispatch.enqueue(
            workflow_run_id="run-dispatch-reader",
            provider="xiaohongshu",
            source_kind="search_result",
            limit=5,
        )
        claim = await dispatch.claim_next(owner="dispatch-worker", lease_seconds=120)
        assert claim is not None
        context = DispatchLeaseContext(
            workflow_run_id="run-dispatch-reader",
            lease_owner="dispatch-worker",
            lease_token=str(claim["lease_token"]),
        )
        store = SQLiteContentResearchStore(
            str(database),
            writer=writer,
        ).for_dispatch_context(context)

        assert store.list_scope_contracts("run-dispatch-reader") == []

        stale_store = SQLiteContentResearchStore(
            str(database),
            writer=writer,
        ).for_dispatch_context(
            DispatchLeaseContext(
                workflow_run_id=context.workflow_run_id,
                lease_owner=context.lease_owner,
                lease_token="stale-lease-token",
            )
        )
        with pytest.raises(ExecutionLeaseFencedError):
            stale_store.list_scope_contracts("run-dispatch-reader")
    finally:
        await writer.close()


@pytest.mark.asyncio
async def test_execution_lease_guard_is_serialized_by_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "execution-lease-guard.sqlite"
    bootstrap_store = SQLiteContentResearchStore(str(database))
    contract = build_scope_contract(
        workflow_run_id="run-execution-lease-guard",
        research_plan_id="plan-execution-lease-guard",
        version=1,
        constraints=(ScopeConstraint("core_object", "core", "shirt", "required"),),
        query_groups=(ScopeQueryGroupInput("shirt", "shirt"),),
    )
    bootstrap_store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="coverage-execution-lease-guard",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    bootstrap_store.save_coverage_snapshot(snapshot)
    authorization = ScopeExecutionAuthorization(
        id="authorization-execution-lease-guard",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        coverage_snapshot_id=snapshot.id,
        resolution="expand_required_constraint",
        execution_revision=2,
        state="authorized_collection",
    )
    continuation = ScopeExecutionContinuation(
        id="continuation-execution-lease-guard",
        authorization_id=authorization.id,
        workflow_run_id=contract.workflow_run_id,
        execution_revision=2,
        operation="supplementary_collection",
        supplementary_queries=("shirt sunscreen",),
        state="pending",
    )
    bootstrap_store.resolve_coverage_and_authorize_execution_atomically(
        snapshot=snapshot,
        authorization=authorization,
        continuation=continuation,
        event=ScopeAuditEvent(
            id="coverage-resolution-execution-lease-guard",
            workflow_run_id=contract.workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=contract.version,
            event_name="coverage_resolved",
            payload={
                "schema_version": "content_research_scope_audit_event_v1",
                "coverage_snapshot_id": snapshot.id,
                "resolution": "expand_required_constraint",
                "constraint_id": "core_object",
            },
        ),
    )

    writer = RuntimeWriteCoordinator(
        database,
        handlers=production_runtime_write_handlers(),
    )
    await writer.start()
    try:
        store = SQLiteContentResearchStore(str(database), writer=writer)
        unit = store.list_scope_execution_units(contract.workflow_run_id)[0]
        claim = store.claim_execution_unit(execution_unit_id=unit.id, owner="worker-a")
        assert claim is not None and claim.lease_token is not None
        live_context = ExecutionContext(
            execution_unit_id=claim.execution_unit_id,
            attempt_no=claim.attempt_no,
            lease_token=claim.lease_token,
            scope_contract_id=contract.id,
        )

        assert store.execution_context_is_live(live_context, operation="live-check") is True
        stale_context = ExecutionContext(
            execution_unit_id=claim.execution_unit_id,
            attempt_no=claim.attempt_no,
            lease_token="stale-lease-token",
            scope_contract_id=contract.id,
        )
        assert store.execution_context_is_live(stale_context, operation="stale-check") is False
        assert any(
            fact.kind == "lease_fenced" and fact.payload.get("operation") == "stale-check"
            for fact in store.execution_trace(claim.execution_unit_id)
        )
    finally:
        await writer.close()
