import sqlite3
from dataclasses import replace

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.content_research.api_schemas import ContentResearchSourceCollectionRequest
from app.content_research.contracts import build_default_snapshot
from app.content_research.models import ResearchBriefRecord, ResearchPlanRecord
from app.content_research.persistence_models import ReportPublicationRecord, StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import LiteReportReader
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.reporting.read_model import (
    PublishedReportNotFoundError,
    PublishedReportReader,
)
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ExecutionContext,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.service import (
    ContentResearchService,
    WorkflowRunManagerRuntime,
    _governed_input_fingerprint,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import (
    _decision,
    _publication,
)
from tests.unit.test_content_research_report_composer import _snapshot as _governed_snapshot


@pytest.mark.asyncio
async def test_publication_dedupe_keeps_identical_nonempty_claims_distinct_across_scopes(
    tmp_path,
    monkeypatch,
):
    """Removing Scope lineage from publication matching must collapse these reports."""
    db_path = str(tmp_path / "scope-aware-publication-dedupe.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="同声明不同范围")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        await manager.begin_report_finalization(run.run_id)
    brief = ResearchBriefRecord(
        id="rb_scope_dedupe",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="confirmed",
        payload={"schema_version": "content_research_brief_v1"},
    )
    plan = ResearchPlanRecord(
        id="rp_scope_dedupe",
        brief_id=brief.id,
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_plan_v1",
        status="confirmed",
        payload={"schema_version": "content_research_plan_v1"},
    )
    store.save_brief(brief)
    store.save_plan(plan)
    service = ContentResearchService(
        store=store,
        presearch=None,
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )

    def authorize(index: int, mode: str):
        scope = build_scope_contract(
            workflow_run_id=run.run_id,
            research_plan_id=plan.id,
            version=index,
            constraints=(
                ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
                ScopeConstraint("season", "季节", "夏季", mode),
            ),
            query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫", "夏季 长袖衬衫"),),
        )
        store.save_scope_contract(scope)
        coverage = CoverageSnapshot(
            id=f"scv_scope_dedupe_{index}",
            workflow_run_id=run.run_id,
            scope_contract_id=scope.id,
            scope_contract_version=scope.version,
            state="awaiting_scope_decision",
            constraint_counts={},
            unmet_constraint_ids=("season",),
        )
        store.save_coverage_snapshot(coverage)
        authorization = ScopeExecutionAuthorization(
            id=f"sea_scope_dedupe_{index}",
            workflow_run_id=run.run_id,
            scope_contract_id=scope.id,
            scope_contract_version=scope.version,
            coverage_snapshot_id=coverage.id,
            resolution="generate_limited_report",
            execution_revision=2,
            state="authorized_limited_report",
        )
        continuation = ScopeExecutionContinuation(
            id=f"sec_scope_dedupe_{index}",
            authorization_id=authorization.id,
            workflow_run_id=run.run_id,
            execution_revision=2,
            operation="limited_report",
            supplementary_queries=(),
            state="pending",
        )
        event = ScopeAuditEvent(
            id=f"sae_scope_dedupe_{index}",
            workflow_run_id=run.run_id,
            scope_contract_id=scope.id,
            scope_contract_version=scope.version,
            event_name="coverage_resolved",
            payload={
                "schema_version": "content_research_scope_audit_event_v1",
                "coverage_snapshot_id": coverage.id,
                "resolution": "generate_limited_report",
                "constraint_id": None,
            },
        )
        _, _, persisted, _, _ = store.resolve_coverage_and_authorize_execution_atomically(
            snapshot=coverage,
            authorization=authorization,
            continuation=continuation,
            event=event,
        )
        assert persisted.execution_unit_id is not None
        attempt = store.claim_execution_unit(
            execution_unit_id=persisted.execution_unit_id,
            owner=f"worker-{index}",
        )
        assert attempt is not None and attempt.lease_token is not None
        return (
            scope,
            coverage,
            persisted,
            ExecutionContext(
                execution_unit_id=persisted.execution_unit_id,
                attempt_no=attempt.attempt_no,
                lease_token=attempt.lease_token,
                scope_contract_id=scope.id,
            ),
        )

    base = _governed_snapshot().metadata["governed_snapshot"]

    def governed_for_scope(*, coverage_snapshot, execution_context, **_kwargs):
        scope = next(
            item
            for item in store.list_scope_contracts(run.run_id)
            if item.id == execution_context.scope_contract_id
        )
        return {
            **base,
            "research_plan_id": plan.id,
            "direction_results": [],
            "publication_state": "partial_verified_report",
            "publication_reason": "admitted_claims_available",
            "workflow_execution_state": "completed",
            "executive_summary": "",
            "policy_scope": {
                **base["policy_scope"],
                "direction_set_version": "direction_set_scope_dedupe",
                "direction_ids": ["product_marketing"],
                "report_compose_mode": "template_only",
            },
            "claim_cards": [
                {
                    **base["claim_cards"][0],
                    "claim_type": "use_context",
                    "scope": {"sample": "selected_packets"},
                }
            ],
            "citation_groups": [
                {**base["citation_groups"][0], "admission_decision_id": "cad_1"}
            ],
            "execution_lineage": {
                "scope_contract_id": scope.id,
                "execution_unit_id": execution_context.execution_unit_id,
                "coverage_snapshot_id": coverage_snapshot.id,
                "successful_attempt_no": execution_context.attempt_no,
            },
            "scope_contract": {
                "id": scope.id,
                "version": scope.version,
                "constraints": [
                    {"id": item.id, "value": item.value, "mode": item.mode}
                    for item in scope.constraints
                ],
            },
            "coverage_snapshot": {
                "id": coverage_snapshot.id,
                "state": coverage_snapshot.state,
            },
            "execution_trace": {
                "execution_unit_id": execution_context.execution_unit_id,
                "attempt_no": execution_context.attempt_no,
                "outcome_state": "not_requested",
                "facts": [],
            },
        }

    monkeypatch.setattr(service, "_build_governed_snapshot", governed_for_scope)
    publications = []
    for index, mode in enumerate(("required", "preferred"), start=1):
        scope, _coverage, authorization, context = authorize(index, mode)
        artifact_ref = await service._publish_report_after_workflow_completion(
            workflow_run_id=run.run_id,
            thread_id=thread["id"],
            execution_authorization=authorization,
            execution_context=context,
        )
        assert artifact_ref is not None
        publications.append((scope, artifact_ref["id"]))

    assert publications[0][1] != publications[1][1]
    persisted = [
        item
        for item in store.list_typed_records(ReportPublicationRecord)
        if item.workflow_run_id == run.run_id
    ]
    assert len(persisted) == 2
    assert {item.scope_contract_id for item in persisted} == {
        publications[0][0].id,
        publications[1][0].id,
    }
    snapshots = store.list_result_snapshots_for_workflow(run.run_id)
    assert [
        snapshot.metadata["governed_snapshot"]["claim_cards"][0]["statement"]
        for snapshot in snapshots
    ] == ["样本直接提到通勤场景。", "样本直接提到通勤场景。"]


@pytest.mark.asyncio
async def test_failed_materialization_retry_reuses_the_exact_persisted_execution_publication(
    tmp_path,
):
    """A newer valid publication must not replace the exact failed publication on retry."""
    db_path = str(tmp_path / "failed-materialization-lineage-retry.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="发布恢复")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 1}],
        )
        await manager.start_step(run.run_id, "formal_research")
        await manager.complete_step(run.run_id, "formal_research")
        await manager.begin_report_finalization(run.run_id)
    brief = ResearchBriefRecord(
        id="rb_publication_retry",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="confirmed",
        payload={"schema_version": "content_research_brief_v1"},
    )
    store.save_brief(brief)
    scope = build_scope_contract(
        workflow_run_id=run.run_id,
        research_plan_id="rp_1",
        version=1,
        constraints=(ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("长袖衬衫", "长袖衬衫"),),
    )
    store.save_scope_contract(scope)
    coverage = CoverageSnapshot(
        id="scv_publication_retry",
        workflow_run_id=run.run_id,
        scope_contract_id=scope.id,
        scope_contract_version=scope.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(coverage)
    unit, _created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=coverage,
        decision={"resolution": "generate_limited_report"},
    )
    attempt = store.claim_execution_unit(execution_unit_id=unit.id, owner="worker-1")
    assert attempt is not None
    base = _governed_snapshot()
    governed = {
        **base.metadata["governed_snapshot"],
        "research_plan_id": "rp_1",
        "direction_results": [],
        "policy_scope": {
            **base.metadata["governed_snapshot"]["policy_scope"],
            "report_compose_mode": "template_only",
            "direction_ids": ["product_marketing"],
            "direction_set_version": "direction_set_retry",
        },
        "claim_cards": [
            {
                **base.metadata["governed_snapshot"]["claim_cards"][0],
                "claim_type": "use_context",
                "scope": {"sample": "selected_packets"},
            }
        ],
        "citation_groups": [
            {
                **base.metadata["governed_snapshot"]["citation_groups"][0],
                "admission_decision_id": "cad_1",
            }
        ],
        "execution_lineage": {
            "scope_contract_id": scope.id,
            "execution_unit_id": unit.id,
            "coverage_snapshot_id": coverage.id,
            "successful_attempt_no": attempt.attempt_no,
        },
        "scope_contract": {"id": scope.id, "version": scope.version},
        "coverage_snapshot": {"id": coverage.id, "state": coverage.state},
        "execution_trace": {
            "execution_unit_id": unit.id,
            "attempt_no": attempt.attempt_no,
            "outcome_state": "not_requested",
            "facts": [],
        },
    }
    snapshot = replace(
        base,
        workflow_run_id=run.run_id,
        research_brief_id=brief.id,
        metadata={
            "governed_snapshot": governed,
            "governed_input_fingerprint": _governed_input_fingerprint(governed),
        },
    )
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(
        _decision(draft),
        workflow_run_id=run.run_id,
        scope_contract_id=draft.scope_contract_id,
        execution_unit_id=draft.execution_unit_id,
        coverage_snapshot_id=draft.coverage_snapshot_id,
        attempt_no=draft.attempt_no,
    )
    publication = replace(
        _publication(draft, decision, compose_mode="template_only"),
        workflow_run_id=run.run_id,
        scope_contract_id=draft.scope_contract_id,
        execution_unit_id=draft.execution_unit_id,
        coverage_snapshot_id=draft.coverage_snapshot_id,
        attempt_no=draft.attempt_no,
    )
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())

    async with WorkflowRunManager(db_path) as manager:
        await manager.fail_run(
            run.run_id,
            {
                "code": "report_publication_failed",
                "message": "artifact write failed",
                "publication_id": publication.id,
            },
        )

    newer_scope = build_scope_contract(
        workflow_run_id=run.run_id,
        research_plan_id="rp_1",
        version=2,
        constraints=(ScopeConstraint("core_object", "核心对象", "短袖衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("短袖衬衫", "短袖衬衫"),),
    )
    store.save_scope_contract(newer_scope)
    newer_coverage = CoverageSnapshot(
        id="scv_publication_retry_newer",
        workflow_run_id=run.run_id,
        scope_contract_id=newer_scope.id,
        scope_contract_version=newer_scope.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(newer_coverage)
    newer_unit, _created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=newer_coverage,
        decision={"resolution": "generate_limited_report"},
    )
    newer_attempt = store.claim_execution_unit(
        execution_unit_id=newer_unit.id,
        owner="worker-2",
    )
    assert newer_attempt is not None
    newer_governed = {
        **governed,
        "execution_lineage": {
            "scope_contract_id": newer_scope.id,
            "execution_unit_id": newer_unit.id,
            "coverage_snapshot_id": newer_coverage.id,
            "successful_attempt_no": newer_attempt.attempt_no,
        },
        "scope_contract": {"id": newer_scope.id, "version": newer_scope.version},
        "coverage_snapshot": {
            "id": newer_coverage.id,
            "state": newer_coverage.state,
        },
        "execution_trace": {
            "execution_unit_id": newer_unit.id,
            "attempt_no": newer_attempt.attempt_no,
            "outcome_state": "not_requested",
            "facts": [],
        },
    }
    newer_snapshot = replace(
        snapshot,
        id="rps_publication_retry_newer",
        metadata={
            "governed_snapshot": newer_governed,
            "governed_input_fingerprint": _governed_input_fingerprint(newer_governed),
        },
    )
    newer_draft = ResearchReportComposer().compose(newer_snapshot)
    newer_decision = replace(
        _decision(newer_draft),
        workflow_run_id=run.run_id,
        scope_contract_id=newer_draft.scope_contract_id,
        execution_unit_id=newer_draft.execution_unit_id,
        coverage_snapshot_id=newer_draft.coverage_snapshot_id,
        attempt_no=newer_draft.attempt_no,
    )
    newer_publication = replace(
        _publication(newer_draft, newer_decision, compose_mode="template_only"),
        workflow_run_id=run.run_id,
        scope_contract_id=newer_draft.scope_contract_id,
        execution_unit_id=newer_draft.execution_unit_id,
        coverage_snapshot_id=newer_draft.coverage_snapshot_id,
        attempt_no=newer_draft.attempt_no,
    )
    store.save_result_snapshot(newer_snapshot)
    store.save_report_draft(newer_draft.to_record())
    store.save_report_faithfulness_decision(newer_decision.to_record())
    store.save_report_publication(newer_publication.to_record())

    service = ContentResearchService(
        store=store,
        presearch=None,
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )

    # Simulate a process exit after the atomic retry command commits but
    # before materialization starts. Recovery must use that command's ID.
    async with WorkflowRunManager(db_path) as manager:
        await manager.retry_failed_report_finalization(
            run.run_id, publication_id=publication.id
        )

    await service._retry_failed_report_publication(
        workflow_run_id=run.run_id,
        request=ContentResearchSourceCollectionRequest(
            provider="xiaohongshu", source_kind="search_result", limit=20
        ),
    )

    persisted = [
        item
        for item in store.list_typed_records(ReportPublicationRecord)
        if item.workflow_run_id == run.run_id
    ]
    assert {item.id for item in persisted} == {publication.id, newer_publication.id}
    assert (
        publication.scope_contract_id,
        publication.execution_unit_id,
        publication.coverage_snapshot_id,
        publication.attempt_no,
    ) == (scope.id, unit.id, coverage.id, attempt.attempt_no)
    report = await PublishedReportReader(store, db_path).read(
        workflow_run_id=run.run_id,
        publication_id=publication.id,
    )
    assert report["publication"]["report_publication_id"] == publication.id
    assert report["scope_contract_id"] == scope.id
    async with WorkflowRunManager(db_path) as manager:
        events = await manager.list_events(run.run_id)
        runtime_snapshot = await manager.get_run_snapshot(run.run_id)
    failure = next(event for event in events if event.event_type == "run_failed")
    assert failure.payload_json["publication_id"] == publication.id
    assert [
        (artifact.get("payload_json") or {}).get("report_publication_id")
        for artifact in runtime_snapshot["artifacts"]
        if (artifact.get("payload_json") or {}).get("report_publication_id") is not None
    ] == [publication.id]


@pytest.mark.asyncio
async def test_published_report_is_flagged_when_its_frozen_attempt_later_fails(
    tmp_path,
):
    db_path = str(tmp_path / "published-report-integrity-flag.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="报告完整性")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")

    scope = build_scope_contract(
        workflow_run_id=run.run_id,
        research_plan_id="rp_integrity",
        version=1,
        constraints=(ScopeConstraint("core_object", "核心对象", "衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("衬衫", "衬衫"),),
    )
    store.save_scope_contract(scope)
    coverage = CoverageSnapshot(
        id="scv_integrity",
        workflow_run_id=run.run_id,
        scope_contract_id=scope.id,
        scope_contract_version=scope.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(coverage)
    unit, _created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=coverage,
        decision={"resolution": "generate_limited_report"},
    )
    claim = store.claim_execution_unit(execution_unit_id=unit.id, owner="worker-integrity")
    assert claim is not None and claim.lease_token is not None

    base = _governed_snapshot()
    governed = {
        **base.metadata["governed_snapshot"],
        "research_plan_id": "rp_integrity",
        "direction_results": [],
        "policy_scope": {
            **base.metadata["governed_snapshot"]["policy_scope"],
            "report_compose_mode": "template_only",
            "direction_ids": ["product_marketing"],
        },
        "execution_lineage": {
            "scope_contract_id": scope.id,
            "execution_unit_id": unit.id,
            "coverage_snapshot_id": coverage.id,
            "successful_attempt_no": claim.attempt_no,
        },
        "scope_contract": {"id": scope.id, "version": scope.version},
        "coverage_snapshot": {"id": coverage.id, "state": coverage.state},
        "execution_trace": {
            "execution_unit_id": unit.id,
            "attempt_no": claim.attempt_no,
            "outcome_state": "not_requested",
            "facts": [],
        },
    }
    snapshot = replace(
        base,
        id="rrs_integrity",
        workflow_run_id=run.run_id,
        research_plan_id="rp_integrity",
        metadata={
            "governed_snapshot": governed,
            "governed_input_fingerprint": _governed_input_fingerprint(governed),
        },
    )
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(
        _decision(draft),
        workflow_run_id=run.run_id,
        scope_contract_id=draft.scope_contract_id,
        execution_unit_id=draft.execution_unit_id,
        coverage_snapshot_id=draft.coverage_snapshot_id,
        attempt_no=draft.attempt_no,
    )
    publication = replace(
        _publication(draft, decision, compose_mode="template_only"),
        workflow_run_id=run.run_id,
        scope_contract_id=draft.scope_contract_id,
        execution_unit_id=draft.execution_unit_id,
        coverage_snapshot_id=draft.coverage_snapshot_id,
        attempt_no=draft.attempt_no,
    )
    publication = replace(publication, publication_state="evidence_only_report")
    successor_scope = build_scope_contract(
        workflow_run_id=run.run_id,
        research_plan_id="rp_integrity",
        version=2,
        constraints=(ScopeConstraint("core_object", "核心对象", "外套", "required"),),
        query_groups=(ScopeQueryGroupInput("外套", "外套"),),
    )
    store.save_scope_contract(successor_scope)
    successor_coverage = CoverageSnapshot(
        id="scv_integrity_successor",
        workflow_run_id=run.run_id,
        scope_contract_id=successor_scope.id,
        scope_contract_version=successor_scope.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(successor_coverage)
    successor_unit, _created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=successor_coverage,
        decision={"resolution": "generate_limited_report"},
    )
    successor_claim = store.claim_execution_unit(
        execution_unit_id=successor_unit.id, owner="worker-successor"
    )
    assert successor_claim is not None and successor_claim.lease_token is not None
    assert store.complete_execution_unit(
        execution_unit_id=successor_unit.id,
        attempt_no=successor_claim.attempt_no,
        owner="worker-successor",
        lease_token=successor_claim.lease_token,
        state="completed",
    )
    successor_governed = {
        **governed,
        "execution_lineage": {
            "scope_contract_id": successor_scope.id,
            "execution_unit_id": successor_unit.id,
            "coverage_snapshot_id": successor_coverage.id,
            "successful_attempt_no": successor_claim.attempt_no,
        },
        "scope_contract": {
            "id": successor_scope.id,
            "version": successor_scope.version,
        },
        "coverage_snapshot": {
            "id": successor_coverage.id,
            "state": successor_coverage.state,
        },
        "execution_trace": {
            "execution_unit_id": successor_unit.id,
            "attempt_no": successor_claim.attempt_no,
            "outcome_state": "not_requested",
            "facts": [],
        },
    }
    successor_snapshot = replace(
        snapshot,
        id="rrs_integrity_successor",
        snapshot_version="2",
        metadata={
            "governed_snapshot": successor_governed,
            "governed_input_fingerprint": _governed_input_fingerprint(
                successor_governed
            ),
        },
    )
    successor_draft = ResearchReportComposer().compose(successor_snapshot)
    successor_decision = replace(
        _decision(successor_draft),
        workflow_run_id=run.run_id,
        scope_contract_id=successor_draft.scope_contract_id,
        execution_unit_id=successor_draft.execution_unit_id,
        coverage_snapshot_id=successor_draft.coverage_snapshot_id,
        attempt_no=successor_draft.attempt_no,
    )
    successor = replace(
        _publication(successor_draft, successor_decision, compose_mode="template_only"),
        workflow_run_id=run.run_id,
        publication_state="evidence_only_report",
        previous_version_id=publication.id,
        scope_contract_id=successor_draft.scope_contract_id,
        execution_unit_id=successor_draft.execution_unit_id,
        coverage_snapshot_id=successor_draft.coverage_snapshot_id,
        attempt_no=successor_draft.attempt_no,
    )
    store.save_result_snapshot(snapshot)
    store.save_result_snapshot(successor_snapshot)
    for item in (draft, successor_draft):
        store.save_report_draft(item.to_record())
    for item in (decision, successor_decision):
        store.save_report_faithfulness_decision(item.to_record())
    for item in (publication, successor):
        store.save_report_publication(item.to_record())

    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)
    materializer = ReportPublicationMaterializer(store, db_path)
    await materializer.materialize(publication.id)
    await materializer.materialize(successor.id)
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_report_finalization(run.run_id)

    assert store.complete_execution_unit(
        execution_unit_id=unit.id,
        attempt_no=claim.attempt_no,
        owner="worker-integrity",
        lease_token=claim.lease_token,
        state="failed",
    )

    reader = PublishedReportReader(store, db_path)
    flagged = await reader.read(
        workflow_run_id=run.run_id,
        publication_id=publication.id,
    )
    lite_flagged = (
        await ContentResearchService(
            store=store,
            presearch=None,
            workflow_runtime=WorkflowRunManagerRuntime(db_path),
        ).get_lite_report(
            workflow_run_id=run.run_id,
            publication_id=publication.id,
        )
    ).model_dump(mode="json")
    current = await reader.read(workflow_run_id=run.run_id)
    assert flagged["integrity_state"] == "integrity_flagged"
    assert flagged["integrity_reason"] == "frozen_execution_attempt_failed"
    assert flagged["integrity_recovery"] == {
        "required_action": "use_successor_publication",
        "successor_publication_id": successor.id,
    }
    assert flagged["publication"]["report_publication_id"] == publication.id
    assert lite_flagged["publication"]["integrity_state"] == "integrity_flagged"
    assert lite_flagged["publication"]["integrity_reason"] == (
        "frozen_execution_attempt_failed"
    )
    assert lite_flagged["publication"]["integrity_recovery"] == (
        flagged["integrity_recovery"]
    )
    assert lite_flagged["integrity_state"] == "integrity_flagged"
    assert lite_flagged["integrity_reason"] == "frozen_execution_attempt_failed"
    assert lite_flagged["integrity_recovery"] == flagged["integrity_recovery"]
    assert current["integrity_state"] == "healthy"
    assert current["publication"]["report_publication_id"] == successor.id
    assert current["publication"]["previous_version_id"] == publication.id
    assert store.get_typed_record(ReportPublicationRecord, publication.id) == (
        publication.to_record()
    )
    with pytest.raises(ValueError, match="integrity-flagged"):
        await materializer.materialize(publication.id)


@pytest.mark.asyncio
async def test_selected_historical_publication_freezes_semantic_scope_coverage_and_trace(
    tmp_path,
):
    """Dropping report lineage from identity/readback must collapse R1 into latest Scope."""
    db_path = str(tmp_path / "historical-publication-lineage.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="历史报告")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")

    scopes = (
        build_scope_contract(
            workflow_run_id=run.run_id,
            research_plan_id="rp_1",
            version=1,
            constraints=(
                ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
                ScopeConstraint("season", "季节", "夏季", "required"),
            ),
            query_groups=(
                ScopeQueryGroupInput("夏季 长袖衬衫", "夏季 长袖衬衫"),
            ),
        ),
        build_scope_contract(
            workflow_run_id=run.run_id,
            research_plan_id="rp_1",
            version=2,
            constraints=(
                ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
                ScopeConstraint("season", "季节", "夏季", "preferred"),
            ),
            query_groups=(
                ScopeQueryGroupInput("通勤 长袖衬衫", "通勤 长袖衬衫"),
            ),
        ),
    )
    base = _governed_snapshot()
    base_governed = base.metadata["governed_snapshot"]
    snapshots = []
    coverages = []
    publications = []
    for index, scope in enumerate(scopes, start=1):
        store.save_scope_contract(scope)
        coverage = CoverageSnapshot(
            id=f"scv_historical_{index}",
            workflow_run_id=run.run_id,
            scope_contract_id=scope.id,
            scope_contract_version=scope.version,
            state="awaiting_scope_decision",
            constraint_counts={
                "_summary": {
                    "minimum_samples": 1,
                    "minimum_independent_authors": 1,
                    "reason_codes": ["required_constraint_coverage_unmet:season"],
                }
            },
            unmet_constraint_ids=("season",),
        )
        store.save_coverage_snapshot(coverage)
        unit, created = store.resolve_coverage_to_execution_unit_atomically(
            snapshot=coverage,
            decision={"resolution": "generate_limited_report"},
        )
        assert created is True
        claim = store.claim_execution_unit(execution_unit_id=unit.id, owner=f"worker-{index}")
        assert claim is not None
        frozen_trace = {
            "execution_unit_id": unit.id,
            "attempt_no": claim.attempt_no,
            "outcome_state": "not_requested",
            "facts": [
                {
                    "attempt_no": fact.attempt_no,
                    "sequence_no": fact.sequence_no,
                    "kind": fact.kind,
                    "payload": {},
                }
                for fact in store.execution_trace(unit.id)
            ],
        }
        scope_payload = {
            "id": scope.id,
            "version": scope.version,
            "constraints": [
                {"id": item.id, "value": item.value, "mode": item.mode}
                for item in scope.constraints
            ],
            "query_groups": [
                {"id": item.id, "final_query": item.final_query}
                for item in scope.query_groups
            ],
        }
        coverage_payload = {
            "id": coverage.id,
            "state": coverage.state,
            "constraint_counts": coverage.constraint_counts,
            "unmet_constraint_ids": list(coverage.unmet_constraint_ids),
        }
        governed = {
            **base_governed,
            "research_plan_id": "rp_1",
            "policy_scope": {
                **base_governed["policy_scope"],
                "direction_set_version": "direction_set_historical",
                "direction_ids": ["product_marketing"],
                "report_compose_mode": "template_only",
            },
            "direction_results": [],
            "claim_cards": [],
            "citation_groups": [],
            "weak_signals": [],
            "cross_direction_records": [],
            "aggregate_claims": [],
            "marketing_conclusions": [],
            "execution_lineage": {
                "scope_contract_id": scope.id,
                "execution_unit_id": unit.id,
                "coverage_snapshot_id": coverage.id,
                "successful_attempt_no": claim.attempt_no,
            },
            "scope_contract": scope_payload,
            "coverage_snapshot": coverage_payload,
            "execution_trace": frozen_trace,
        }
        snapshot = replace(
            base,
            id=f"rrs_historical_{index}",
            workflow_run_id=run.run_id,
            snapshot_version=str(index),
            metadata={
                "governed_snapshot": governed,
                "governed_input_fingerprint": _governed_input_fingerprint(governed),
            },
        )
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(
            _decision(draft),
            workflow_run_id=run.run_id,
            scope_contract_id=draft.scope_contract_id,
            execution_unit_id=draft.execution_unit_id,
            coverage_snapshot_id=draft.coverage_snapshot_id,
            attempt_no=draft.attempt_no,
        )
        publication = replace(
            _publication(draft, decision, compose_mode="template_only"),
            workflow_run_id=run.run_id,
            scope_contract_id=draft.scope_contract_id,
            execution_unit_id=draft.execution_unit_id,
            coverage_snapshot_id=draft.coverage_snapshot_id,
            attempt_no=draft.attempt_no,
        )
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        snapshots.append(snapshot)
        coverages.append(coverage)
        publications.append(publication)

    assert snapshots[0].metadata["governed_input_fingerprint"] != snapshots[1].metadata[
        "governed_input_fingerprint"
    ]
    assert publications[0].id != publications[1].id

    foreign_coverage_publication = replace(
        publications[1].to_record(),
        id="rpp_foreign_coverage",
        coverage_snapshot_id=coverages[0].id,
    )
    with pytest.raises(ValueError, match="report.*lineage mismatch"):
        store.save_report_publication(foreign_coverage_publication)

    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)
    materializer = ReportPublicationMaterializer(store, db_path)
    for publication in publications:
        await materializer.materialize(publication.id)
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_report_finalization(run.run_id)

    reader = PublishedReportReader(store, db_path)
    first = await reader.read(
        workflow_run_id=run.run_id,
        publication_id=publications[0].id,
    )
    second = await reader.read(
        workflow_run_id=run.run_id,
        publication_id=publications[1].id,
    )

    assert first["scope_contract_id"] == scopes[0].id
    assert second["scope_contract_id"] == scopes[1].id
    assert first["scope_contract"]["constraints"][1]["mode"] == "required"
    assert second["scope_contract"]["constraints"][1]["mode"] == "preferred"
    assert first["coverage_snapshot"]["id"] == "scv_historical_1"
    assert second["coverage_snapshot"]["id"] == "scv_historical_2"
    assert first["trace"]["execution"]["execution_unit_id"] != second["trace"][
        "execution"
    ]["execution_unit_id"]

    lite_first = await LiteReportReader(store, db_path).read(
        workflow_run_id=run.run_id,
        publication_id=publications[0].id,
    )
    lite_second = await LiteReportReader(store, db_path).read(
        workflow_run_id=run.run_id,
        publication_id=publications[1].id,
    )
    assert lite_first["frozen_scope"]["scope_contract_id"] == scopes[0].id
    assert lite_second["frozen_scope"]["scope_contract_id"] == scopes[1].id
    assert lite_first["frozen_scope"]["coverage_snapshot_id"] == coverages[0].id
    assert lite_second["frozen_scope"]["coverage_snapshot_id"] == coverages[1].id
    assert lite_first["publication"]["execution_unit_id"] != lite_second["publication"][
        "execution_unit_id"
    ]
    assert lite_first["publication"]["attempt_no"] == 0

    with sqlite3.connect(db_path) as connection:
        for table in (
            "content_research_report_drafts",
            "content_research_report_faithfulness_decisions",
            "content_research_report_publications",
        ):
            connection.execute(
                f"UPDATE {table} SET scope_contract_id=NULL, execution_unit_id=NULL, "
                "coverage_snapshot_id=NULL, attempt_no=NULL WHERE workflow_run_id=? "
                "AND governed_snapshot_id=?",
                (run.run_id, snapshots[0].id),
            )

    with pytest.raises(
        PublishedReportNotFoundError, match="frozen execution lineage mismatch"
    ):
        await reader.read(
            workflow_run_id=run.run_id,
            publication_id=publications[0].id,
        )
    with pytest.raises(ValueError, match="governed snapshot execution lineage mismatch"):
        await materializer.materialize(publications[0].id)


@pytest.mark.asyncio
async def test_creator_timeline_api_exposes_one_materialized_report_result_and_replay_is_idempotent(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "published-report-timeline.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    original_thread_store = getattr(app.state, "thread_store", None)
    app.state.thread_store = thread_store
    try:
        thread = await thread_store.create_thread(title="调研报告")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        store.save_brief(
            ResearchBriefRecord(
                id="brief_existing_publication_selector_mismatch",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_brief_v1",
                status="ready",
                payload={
                    "schema_version": "content_research_brief_payload_v1",
                    "confirmed_subject": "已有报告不应进入恢复",
                },
            )
        )
        governed = _governed_snapshot().metadata["governed_snapshot"]
        frozen_citations = [
            {
                **governed["citation_groups"][0],
                "citation_group_id": "cg_1",
                "display_index": 1,
                "admission_decision_id": "cad_1",
                "evidence_refs": [
                    {
                        **governed["citation_groups"][0]["evidence_refs"][0],
                        "canonical_note_id": "note_1",
                        "source_url": "https://www.xiaohongshu.com/explore/note_1",
                    }
                ],
            },
            {
                **governed["citation_groups"][0],
                "citation_group_id": "cg_2",
                "display_index": 2,
                "admission_decision_id": "cad_1",
                "evidence_refs": [
                    {
                        **governed["citation_groups"][0]["evidence_refs"][0],
                        "canonical_note_id": "note_1",
                        "source_url": "https://www.xiaohongshu.com/explore/note_1",
                    }
                ],
            },
        ]
        snapshot = replace(
            _governed_snapshot(),
            workflow_run_id=run.run_id,
            metadata={
                "governed_snapshot": {
                    **governed,
                    "claim_cards": [
                        {
                            **governed["claim_cards"][0],
                            "claim_type": "use_context",
                            "scope": {"sample": "selected_packets"},
                        }
                    ],
                    "policy_scope": {
                        **governed["policy_scope"],
                        "direction_set_version": "direction_set_v1",
                        "direction_ids": ["product_marketing"],
                        "report_compose_mode": "template_only",
                    },
                    "citation_groups": frozen_citations,
                },
                "governed_input_fingerprint": "timeline_frozen_citations",
            },
        )
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(
            _publication(draft, decision, compose_mode="template_only"),
            workflow_run_id=run.run_id,
        )
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        materializer = ReportPublicationMaterializer(store, db_path)
        artifact = await materializer.materialize(publication.id)
        await materializer.materialize(publication.id)
        # The artifact is private until publication finalization commits the
        # workflow.  A finalizing run must not look like a completed report.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            finalizing_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report"
            )
            finalizing_timeline = await client.get(f"/threads/{thread['id']}/timeline")
        assert finalizing_response.status_code == 404, finalizing_response.text
        assert not [
            message
            for message in finalizing_timeline.json()["messages"]
            if message["message_type"] == "artifact_result" and message["run_id"] == run.run_id
        ]
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)
        await materializer.publish_timeline_message(publication.id)
        await materializer.publish_timeline_message(publication.id)
        # A committed publication must not be replaced by a later failed
        # checkpoint, and a selector that does not resolve to it stays absent.
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id="checkpoint_existing_publication_selector_mismatch",
                schema_version="content_research_stage_checkpoint_v1",
                payload={"reason_code": "auth_expired"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_existing_publication_selector_mismatch",
                stage_name="collect",
                input_fingerprint="existing-publication-selector-mismatch",
                status="failed",
            )
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/threads/{thread['id']}/timeline")
            lite_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report?citation_group_ids=cg_1"
            )
            unavailable_citation_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report?citation_group_ids=cg_missing"
            )
            selector_mismatch_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report?publication_id=publication_missing"
            )
            report_response = await client.get(f"/content-research/workflows/{run.run_id}/report")
            legacy_response = await client.get(f"/content-research/workflows/{run.run_id}/results")
        assert response.status_code == 200
        results = [
            message
            for message in response.json()["messages"]
            if message["message_type"] == "artifact_result" and message["run_id"] == run.run_id
        ]
        assert len(results) == 1
        reference = results[0]["artifact_refs"][0]
        assert reference["artifact_id"] == artifact.artifact_id
        assert reference["artifact"]["payload_mode"] == "snapshot"
        assert (
            reference["artifact"]["materialized_payload_json"]["report_publication_id"]
            == publication.id
        )
        assert report_response.status_code == 404, report_response.text
        assert lite_response.status_code == 200, lite_response.text
        assert unavailable_citation_response.status_code == 404
        assert selector_mismatch_response.status_code == 404
        assert legacy_response.status_code == 404
        lite = lite_response.json()
        assert lite["publication"]["state"] == "complete_verified_report"
        assert isinstance(lite["status_strip"]["admitted_finding_count"], int)
        assert [item["citation_group_id"] for item in lite["citations"]] == ["cg_1"]
        assert [
            ref["quote"]
            for citation in lite["citations"]
            for ref in citation["evidence_refs"]
        ] == ["通勤"]
    finally:
        app.state.thread_store = original_thread_store
        await thread_store.close()


@pytest.mark.asyncio
async def test_lite_report_api_exposes_recovery_as_non_report_projection(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lite-recovery-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    original_thread_store = getattr(app.state, "thread_store", None)
    app.state.thread_store = thread_store
    try:
        thread = await thread_store.create_thread(title="恢复调研")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
            await manager.fail_run(
                run.run_id, {"code": "auth_expired", "message": "Cookie expired"}
            )
        store.save_brief(
            ResearchBriefRecord(
                id="brief_recovery_api",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_brief_v1",
                status="ready",
                payload={
                    "schema_version": "content_research_brief_payload_v1",
                    "confirmed_subject": "Satisfy Running",
                },
            )
        )
        policy, _, _ = build_default_snapshot(
            snapshot_id="policy_recovery_api",
            workflow_run_id=run.run_id,
            brief_id="brief_recovery_api",
            plan_id="plan_recovery_api",
            direction_set_version="direction_set_v1",
            direction_ids=("product_marketing", "competitor_discovery", "content_performance"),
            report_compose_mode="template_only",
        )
        store.save_run_policy_snapshot(policy)
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                "checkpoint_recovery_api",
                "content_research_stage_checkpoint_v1",
                {"reason_code": "auth_expired"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_recovery_api",
                stage_name="collect",
                input_fingerprint="frozen-operation",
                status="failed",
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/content-research/workflows/{run.run_id}/lite-report")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["publication"] == {"state": None}
        assert payload["recovery_projection"]["reason_code"] == "auth_expired"
        assert payload["recovery_projection"]["next_action"] == "resume_run"
        assert payload["frozen_scope"]["report_compose_mode"] == "template_only"
        assert len(payload["run_direction_states"]) == 3
    finally:
        app.state.thread_store = original_thread_store
        await thread_store.close()
