"""Read-only Content Research application boundary.

The protocol is the stable dependency for HTTP readers.  The legacy adapter is
temporary migration scaffolding: it keeps every public response and error
mapping unchanged while read implementations move out of the application
service one family at a time.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Protocol

from app.content_research.api_schemas import (
    ContentResearchBriefResponse,
    ContentResearchDirectionEvidenceResponse,
    ContentResearchDirectionResponse,
    ContentResearchGovernanceResponse,
    ContentResearchHistoricalWorkflowSummaryResponse,
    ContentResearchLiteReportResponse,
    ContentResearchPlanResponse,
    ContentResearchPresearchResponse,
    ContentResearchRunProjectionResponse,
    ContentResearchScopeProjectionResponse,
    ContentResearchSubagentTaskResponse,
    ContentResearchTraceResponse,
    ContentResearchWorkflowEventsResponse,
    ContentResearchWorkflowSummaryResponse,
    HumanDecisionsResponse,
)
from app.content_research.errors import (
    ContentResearchNotFoundError,
    ContentResearchReportIntegrityError,
    ContentResearchRunNotFoundError,
    ContentResearchSnapshotBehindError,
    ContentResearchSnapshotUnavailableError,
    ContentResearchValidationError,
)
from app.content_research.evidence.governance_reader import (
    GovernanceReadModelReader,
    safe_public_projection,
)
from app.content_research.evidence.packet_reader import PacketEvidenceReader
from app.content_research.lifecycle.coordinator import (
    ContentResearchPersistenceCoordinator,
)
from app.content_research.lifecycle.models import ContentResearchState, RunProjection
from app.content_research.observation import ContentResearchTraceService
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    DirectionResultDecisionRecord,
    ReportPublicationRecord,
    WeakSignalRecord,
)
from app.content_research.projections import (
    recovery_plan_payload,
    run_projection_payload,
    safe_read_model,
)
from app.content_research.reporting.lite_read_model import LiteReportReader
from app.content_research.reporting.read_model import PublishedReportNotFoundError
from app.content_research.scope_projection import (
    coverage_snapshot_payload,
    scope_audit_payload,
    scope_contract_payload,
    scope_decision_recovery,
    scope_draft_audit_payload,
    scope_draft_payload,
    scope_execution_unit_projection,
    scope_projection_resolutions,
    scope_query_input_payload,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.consistent_snapshot_reader import (
    ConsistentSnapshotReader,
    SnapshotBehind,
    SnapshotFound,
    SnapshotNotFound,
    SnapshotUnavailable,
)


class WorkflowReadRuntime(Protocol):
    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict: ...

    async def list_events(self, workflow_run_id: str) -> list[dict]: ...


class ContentResearchQuery(Protocol):
    """Public read operations; implementations must not mutate business state."""

    def get_presearch(self, attempt_id: str) -> ContentResearchPresearchResponse: ...

    async def get_workflow_summary(
        self, workflow_run_id: str
    ) -> ContentResearchWorkflowSummaryResponse | ContentResearchHistoricalWorkflowSummaryResponse: ...

    def get_policy_snapshot(self, workflow_run_id: str) -> dict: ...

    async def list_workflow_events(
        self, workflow_run_id: str
    ) -> ContentResearchWorkflowEventsResponse: ...

    async def get_scope_projection(
        self, workflow_run_id: str, *, version: int | None = None
    ) -> ContentResearchScopeProjectionResponse: ...

    async def get_workflow_trace(
        self, workflow_run_id: str, *, minimum_revision: int | None = None
    ) -> ContentResearchTraceResponse: ...

    def list_human_decisions(self, workflow_run_id: str) -> HumanDecisionsResponse: ...

    async def get_lite_report(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
        citation_group_ids: list[str] | None = None,
    ) -> ContentResearchLiteReportResponse: ...

    def get_direction_evidence(
        self,
        *,
        workflow_run_id: str,
        direction_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchDirectionEvidenceResponse: ...

    def get_governance_read_model(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchGovernanceResponse: ...


class LegacyContentResearchQueryAdapter:
    """Temporary read-only adapter over the pre-refactor application service."""

    def __init__(self, source: ContentResearchQuery) -> None:
        self._source = source

    def is_for(self, source: ContentResearchQuery) -> bool:
        return self._source is source

    def get_presearch(self, attempt_id: str) -> ContentResearchPresearchResponse:
        return self._source.get_presearch(attempt_id)

    async def get_workflow_summary(
        self, workflow_run_id: str
    ) -> ContentResearchWorkflowSummaryResponse | ContentResearchHistoricalWorkflowSummaryResponse:
        return await self._source.get_workflow_summary(workflow_run_id)

    def get_policy_snapshot(self, workflow_run_id: str) -> dict:
        return self._source.get_policy_snapshot(workflow_run_id)

    async def list_workflow_events(
        self, workflow_run_id: str
    ) -> ContentResearchWorkflowEventsResponse:
        return await self._source.list_workflow_events(workflow_run_id)

    async def get_scope_projection(
        self, workflow_run_id: str, *, version: int | None = None
    ) -> ContentResearchScopeProjectionResponse:
        return await self._source.get_scope_projection(workflow_run_id, version=version)

    async def get_workflow_trace(
        self, workflow_run_id: str, *, minimum_revision: int | None = None
    ) -> ContentResearchTraceResponse:
        return await self._source.get_workflow_trace(
            workflow_run_id, minimum_revision=minimum_revision
        )

    def list_human_decisions(self, workflow_run_id: str) -> HumanDecisionsResponse:
        return self._source.list_human_decisions(workflow_run_id)

    async def get_lite_report(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
        citation_group_ids: list[str] | None = None,
    ) -> ContentResearchLiteReportResponse:
        return await self._source.get_lite_report(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            publication_id=publication_id,
            citation_group_ids=citation_group_ids,
        )

    def get_direction_evidence(
        self,
        *,
        workflow_run_id: str,
        direction_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchDirectionEvidenceResponse:
        return self._source.get_direction_evidence(
            workflow_run_id=workflow_run_id,
            direction_id=direction_id,
            offset=offset,
            limit=limit,
        )

    def get_governance_read_model(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchGovernanceResponse:
        return self._source.get_governance_read_model(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            offset=offset,
            limit=limit,
        )


class ContentResearchQueryService(LegacyContentResearchQueryAdapter):
    """Read boundary that owns migrated projections and delegates the rest."""

    def __init__(
        self,
        source: ContentResearchQuery,
        *,
        store: SQLiteContentResearchStore,
        lifecycle: ContentResearchPersistenceCoordinator,
        workflow_runtime: WorkflowReadRuntime,
    ) -> None:
        super().__init__(source)
        self._store = store
        self._lifecycle = lifecycle
        self._workflow_runtime = workflow_runtime
        self._snapshot_reader = ConsistentSnapshotReader(
            Path(self._store._db_path),
            domain_trace_loader=self._load_domain_trace_snapshot,
        )

    def get_policy_snapshot(self, workflow_run_id: str) -> dict[str, Any]:
        snapshot = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if snapshot is None:
            raise ContentResearchNotFoundError(
                f"Policy snapshot not found for workflow: {workflow_run_id}"
            )
        contracts = self._store.list_direction_contracts(snapshot.id)
        policies = [self._store.get_sample_policy(item.sample_policy_id) for item in contracts]
        return {
            "schema_version": "content_research_policy_snapshot_response_v1",
            "id": snapshot.id,
            "workflow_run_id": snapshot.workflow_run_id,
            "effective_policy": snapshot.effective_policy,
            "effective_policy_hash": snapshot.effective_policy_hash,
            "validation_result": snapshot.validation_result,
            "run_as_of_at": snapshot.run_as_of_at.isoformat(),
            "sample_policies": [asdict(item) for item in policies if item is not None],
            "direction_contracts": [asdict(item) for item in contracts],
        }

    async def get_workflow_summary(
        self, workflow_run_id: str
    ) -> ContentResearchWorkflowSummaryResponse | ContentResearchHistoricalWorkflowSummaryResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        plans = self._store.list_plans_for_brief(brief.id) if brief is not None else []
        plan = plans[-1] if plans else None
        directions = self._store.list_directions_for_plan(plan.id) if plan else []
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        try:
            run_projection = await self._lifecycle.load(workflow_run_id)
        except ValueError as exc:
            if "historical workflow run" not in str(exc):
                raise
            if brief is None:
                raise ContentResearchNotFoundError(
                    f"Content research workflow not found: {workflow_run_id}"
                ) from exc
            historical = await self._lifecycle.load_historical_read_only(workflow_run_id)
            return ContentResearchHistoricalWorkflowSummaryResponse(
                workflow_run_id=workflow_run_id,
                historical_run=historical,
                brief=ContentResearchBriefResponse(
                    id=brief.id,
                    workflow_run_id=brief.workflow_run_id,
                    thread_id=brief.thread_id,
                    status=brief.status,
                    payload=brief.payload,
                ),
                plan=(
                    ContentResearchPlanResponse(
                        id=plan.id,
                        brief_id=plan.brief_id,
                        workflow_run_id=plan.workflow_run_id,
                        status=plan.status,
                        payload=plan.payload,
                    )
                    if plan
                    else None
                ),
                directions=[
                    ContentResearchDirectionResponse(
                        id=item.id,
                        name=str(item.payload.get("name") or item.id),
                        direction_type=str(item.payload.get("direction_type") or ""),
                        priority=item.priority,
                        status=item.status,
                        payload=item.payload,
                    )
                    for item in directions
                ],
                subagent_tasks=[
                    ContentResearchSubagentTaskResponse(
                        id=item.id,
                        plan_id=item.plan_id,
                        direction_id=item.direction_id,
                        status=item.status,
                        payload=item.payload,
                    )
                    for item in tasks
                ],
            )
        if (
            run_projection.state is ContentResearchState.REPORT_READY
            and self._publication_repair_available(workflow_run_id)
        ):
            run_projection = replace(run_projection, allowed_actions=("repair_publication",))
        return ContentResearchWorkflowSummaryResponse(
            workflow_run_id=workflow_run_id,
            run=run_projection_payload(run_projection),
            brief=(
                ContentResearchBriefResponse(
                    id=brief.id,
                    workflow_run_id=brief.workflow_run_id,
                    thread_id=brief.thread_id,
                    status=brief.status,
                    payload=brief.payload,
                )
                if brief is not None
                else None
            ),
            plan=(
                ContentResearchPlanResponse(
                    id=plan.id,
                    brief_id=plan.brief_id,
                    workflow_run_id=plan.workflow_run_id,
                    status=plan.status,
                    payload=plan.payload,
                )
                if plan
                else None
            ),
            directions=[
                ContentResearchDirectionResponse(
                    id=item.id,
                    name=str(
                        item.payload.get("name") or item.payload.get("direction_id") or item.id
                    ),
                    direction_type=str(item.payload.get("direction_type") or ""),
                    priority=item.priority,
                    status=item.status,
                    payload=item.payload,
                )
                for item in directions
            ],
            subagent_tasks=[
                ContentResearchSubagentTaskResponse(
                    id=item.id,
                    plan_id=item.plan_id,
                    direction_id=item.direction_id,
                    status=item.status,
                    payload=item.payload,
                )
                for item in tasks
            ],
            local_cache_id=brief.id if brief is not None else None,
        )

    def _publication_repair_available(self, workflow_run_id: str) -> bool:
        publications = sorted(
            (
                item
                for item in self._store.list_typed_records(ReportPublicationRecord)
                if item.workflow_run_id == workflow_run_id
            ),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
        if not publications:
            return False
        events = self._store.list_report_integrity_events(publications[0].id)
        return bool(events and events[-1].reason_code == "materialized_artifact_invalid")

    async def list_workflow_events(
        self, workflow_run_id: str
    ) -> ContentResearchWorkflowEventsResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        return ContentResearchWorkflowEventsResponse(
            workflow_run_id=workflow_run_id,
            events=await self._workflow_runtime.list_events(workflow_run_id),
        )

    async def get_workflow_trace(
        self, workflow_run_id: str, *, minimum_revision: int | None = None
    ) -> ContentResearchTraceResponse:
        result = await self._snapshot_reader.read_domain_trace(
            workflow_run_id,
            minimum_revision=minimum_revision,
        )
        if isinstance(result, SnapshotFound):
            return result.snapshot
        if isinstance(result, SnapshotNotFound):
            raise ContentResearchRunNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        if isinstance(result, SnapshotBehind):
            raise ContentResearchSnapshotBehindError(
                result.observed_revision, result.minimum_revision
            )
        assert isinstance(result, SnapshotUnavailable)
        raise ContentResearchSnapshotUnavailableError(result.code)

    async def _load_domain_trace_snapshot(
        self,
        connection: sqlite3.Connection,
        workflow_run_id: str,
    ) -> tuple[ContentResearchTraceResponse, int] | None:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
        ).fetchone() is None:
            return None
        if connection.execute(
            "SELECT 1 FROM workflow_runs WHERE run_id=?", (workflow_run_id,)
        ).fetchone() is None:
            return None
        run = self._lifecycle._load_sync_in_transaction(connection, workflow_run_id)
        transitions = [
            dict(row)
            for row in connection.execute(
                """SELECT from_state, to_state, event, state_revision,
                          reason_code, attempt_id, created_at
                   FROM content_research_state_transitions
                   WHERE run_id=? ORDER BY state_revision ASC""",
                (workflow_run_id,),
            ).fetchall()
        ]
        trace = await self._get_workflow_trace_from_transaction(
            workflow_run_id=workflow_run_id,
            connection=connection,
            run=run,
            transitions=transitions,
        )
        return trace, trace.trace_revision

    async def _get_workflow_trace_from_transaction(
        self,
        *,
        workflow_run_id: str,
        connection: sqlite3.Connection,
        run: RunProjection,
        transitions: list[dict[str, Any]],
    ) -> ContentResearchTraceResponse:
        snapshot_store = SQLiteContentResearchStore.for_read_transaction(
            self._store._db_path, connection
        )
        brief = snapshot_store.get_brief_by_workflow(workflow_run_id)
        trace = await ContentResearchTraceService(
            store=snapshot_store,
            db_path=self._store._db_path,
            read_transaction_connection=connection,
        ).build_trace(
            workflow_run_id=workflow_run_id,
            brief=brief,
            current_publication_id=run.publication_id,
        )
        stage_by_state = {
            ContentResearchState.PRESEARCH_RUNNING: "presearch",
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED: "brief_confirmation",
            ContentResearchState.SCOPE_CONFIRMATION_REQUIRED: "scope_confirmation",
            ContentResearchState.RETRIEVAL_QUEUED: "retrieval",
            ContentResearchState.RETRIEVAL_RUNNING: "retrieval",
            ContentResearchState.COVERAGE_EVALUATING: "coverage",
            ContentResearchState.COVERAGE_DECISION_REQUIRED: "coverage",
            ContentResearchState.REPORT_COMPOSING: "report",
            ContentResearchState.REPORT_READY: "report",
            ContentResearchState.RECOVERY_REQUIRED: str(
                (run.error or {}).get("stage") or "recovery"
            ),
            ContentResearchState.CANCELLED_OR_FAILED: "terminal",
        }
        if (
            run.state is ContentResearchState.REPORT_COMPOSING
            and trace.effective_attempt is not None
            and trace.effective_attempt.get("state") != "succeeded"
        ):
            stage_by_state[ContentResearchState.REPORT_COMPOSING] = "marketing_analysis"
        status_by_state = {
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED: "waiting_user",
            ContentResearchState.SCOPE_CONFIRMATION_REQUIRED: "waiting_user",
            ContentResearchState.COVERAGE_DECISION_REQUIRED: "waiting_user",
            ContentResearchState.RECOVERY_REQUIRED: "waiting_user",
            ContentResearchState.REPORT_READY: "succeeded",
            ContentResearchState.CANCELLED_OR_FAILED: "failed",
        }
        safe_error = dict(run.error or {})
        recovery_plan = recovery_plan_payload(run)
        return trace.model_copy(
            update={
                "state": run.state.value,
                "state_revision": run.state_revision,
                "state_transitions": transitions,
                "thread_id": run.thread_id,
                "current_stage": stage_by_state[run.state],
                "run_status": status_by_state.get(run.state, "running"),
                "recoverable": (
                    recovery_plan is not None
                ),
                "recovery_plan": recovery_plan,
                "llm_recovery": (
                    {
                        "required": True,
                        "error_code": safe_error.get("code"),
                        "recovery_action": safe_error.get("recovery_action"),
                        "message": safe_error.get("message"),
                    }
                    if recovery_plan is not None
                    and recovery_plan["action"] == "retry_presearch"
                    else {}
                ),
            }
        )

    async def get_scope_projection(
        self, workflow_run_id: str, *, version: int | None = None
    ) -> ContentResearchScopeProjectionResponse:
        run_projection = await self._lifecycle.load(workflow_run_id)
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        draft = self._store.get_latest_scope_draft(workflow_run_id)
        if draft is None:
            raise ContentResearchNotFoundError(
                f"Scope draft not found for workflow: {workflow_run_id}"
            )
        contracts = self._store.list_scope_contracts(workflow_run_id)
        contract = (
            None
            if not contracts
            else contracts[-1]
            if version is None
            else self._store.get_scope_contract(workflow_run_id, version=version)
        )
        if contract is None and contracts:
            requested = str(version) if version is not None else "latest"
            raise ContentResearchNotFoundError(
                f"Scope contract version {requested} not found for workflow: {workflow_run_id}"
            )

        audit_events = [
            scope_draft_audit_payload(event)
            for event in self._store.list_scope_draft_audit_events(
                workflow_run_id, scope_draft_id=draft.id
            )
        ]
        if contract is not None:
            audit_events.extend(
                scope_audit_payload(event)
                for event in self._store.list_scope_audit_events(
                    workflow_run_id, version=contract.version
                )
            )
        authorizations = self._store.list_scope_execution_authorizations(workflow_run_id)
        current_authorization = max(
            (
                item
                for item in authorizations
                if contract is not None
                and item.scope_contract_id == contract.id
                and item.scope_contract_version == contract.version
            ),
            key=lambda item: (item.execution_revision, item.created_at, item.id),
            default=None,
        )
        coverage_snapshot = (
            self._store.get_coverage_snapshot(
                workflow_run_id,
                version=contract.version,
                execution_revision=current_authorization.execution_revision,
            )
            if current_authorization is not None and contract is not None
            else None
        )
        if (
            coverage_snapshot is not None
            and coverage_snapshot.execution_authorization_id != current_authorization.id
        ):
            coverage_snapshot = None
        if current_authorization is None and contract is not None:
            coverage_snapshot = self._store.get_coverage_snapshot(
                workflow_run_id,
                version=contract.version,
                execution_revision=1,
            )
            if (
                coverage_snapshot is not None
                and coverage_snapshot.execution_authorization_id is not None
            ):
                coverage_snapshot = None
        allowed_actions = (
            [
                {
                    "action": "replace_scope_draft",
                    "available": True,
                    "scope_draft_id": draft.id,
                    "query_groups": [
                        scope_query_input_payload(item) for item in draft.query_groups
                    ],
                }
            ]
            if run_projection.state is ContentResearchState.SCOPE_CONFIRMATION_REQUIRED
            and "replace_scope_draft" in run_projection.allowed_actions
            else []
        )
        allowed_resolutions = (
            scope_projection_resolutions(
                contract=contract,
                coverage_snapshot=coverage_snapshot,
                authorizations=authorizations,
            )
            if run_projection.state is ContentResearchState.COVERAGE_DECISION_REQUIRED
            else []
        )
        execution_unit = (
            self._store.get_scope_execution_unit(current_authorization.execution_unit_id)
            if current_authorization is not None
            and current_authorization.execution_unit_id is not None
            else None
        )
        execution_facts = (
            self._store.execution_trace(execution_unit.id) if execution_unit is not None else []
        )
        return ContentResearchScopeProjectionResponse(
            workflow_run_id=workflow_run_id,
            state=run_projection.state.value,
            state_revision=run_projection.state_revision,
            subject_structure_analysis_state=str(
                brief.payload.get("subject_structure_analysis_state") or "unresolved"
            ),
            subject_structure_analysis_reason_codes=tuple(
                brief.payload.get("subject_structure_analysis_reason_codes") or ()
            ),
            run=ContentResearchRunProjectionResponse(
                **run_projection_payload(run_projection)
            ),
            draft=scope_draft_payload(draft),
            scope_contract=scope_contract_payload(contract) if contract is not None else None,
            audit_events=sorted(
                (safe_public_projection(event) for event in audit_events),
                key=lambda event: (str(event["created_at"]), str(event["id"])),
            ),
            allowed_actions=allowed_actions,
            coverage_snapshot=(
                coverage_snapshot_payload(coverage_snapshot)
                if coverage_snapshot is not None
                else None
            ),
            allowed_resolutions=allowed_resolutions,
            decision_recovery=scope_decision_recovery(
                coverage_snapshot=coverage_snapshot,
                authorizations=authorizations,
                allowed_resolutions=allowed_resolutions,
            )
            if run_projection.state is ContentResearchState.COVERAGE_DECISION_REQUIRED
            else None,
            execution_unit=scope_execution_unit_projection(
                execution_unit=execution_unit,
                authorization=current_authorization,
                audit_events=audit_events,
                execution_facts=execution_facts,
            ),
        )

    async def get_lite_report(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
        citation_group_ids: list[str] | None = None,
    ) -> ContentResearchLiteReportResponse:
        try:
            payload = await LiteReportReader(self._store, self._store._db_path).read(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                publication_id=publication_id,
                citation_group_ids=citation_group_ids,
            )
        except PublishedReportNotFoundError as exc:
            message = str(exc)
            if message in {
                "published report not found",
                "published report artifact is missing",
                "published report is not ready",
                "report scope decision is pending",
            } or message.startswith(
                ("requested citation groups are absent", "report scope decision is pending")
            ):
                raise ContentResearchNotFoundError(message) from exc
            raise ContentResearchReportIntegrityError(message) from exc
        return ContentResearchLiteReportResponse(**payload)

    def get_governance_read_model(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchGovernanceResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        plans = self._store.list_plans_for_brief(brief.id)
        if not plans:
            raise ContentResearchNotFoundError(
                f"Content research plan not found for workflow: {workflow_run_id}"
            )
        plan_id = research_plan_id or plans[-1].id
        if not any(item.id == plan_id for item in plans):
            raise ContentResearchNotFoundError(
                f"Content research plan not found for workflow: {plan_id}"
            )
        policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if policy is None:
            raise ContentResearchValidationError(
                "Governance read model requires a frozen policy snapshot"
            )
        try:
            read = GovernanceReadModelReader(self._store).read(
                workflow_run_id=workflow_run_id,
                research_plan_id=plan_id,
                offset=offset,
                limit=limit,
            )
        except ValueError as exc:
            raise ContentResearchValidationError(str(exc)) from exc
        return ContentResearchGovernanceResponse(
            workflow_run_id=read.workflow_run_id,
            research_plan_id=read.research_plan_id,
            governed_snapshot_identity={
                "schema_version": "content_research_governed_snapshot_v2",
                "workflow_run_id": workflow_run_id,
                "research_plan_id": read.research_plan_id,
                "policy_snapshot_id": policy.id,
                "effective_policy_hash": policy.effective_policy_hash,
            },
            cross_direction_records=read.cross_direction_records,
            aggregate_claims=read.aggregate_claims,
            cross_direction_total=read.cross_direction_total,
            aggregate_total=read.aggregate_total,
            offset=read.offset,
            limit=read.limit,
        )

    def get_direction_evidence(
        self,
        *,
        workflow_run_id: str,
        direction_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchDirectionEvidenceResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        if offset < 0 or not 1 <= limit <= 50:
            raise ContentResearchValidationError(
                "offset must be non-negative and limit must be 1..50"
            )
        plans = self._store.list_plans_for_brief(brief.id)
        direction_records = self._store.list_directions_for_plan(plans[-1].id) if plans else []
        known_directions = {
            value
            for item in direction_records
            for value in (
                str(item.payload.get("direction_id") or ""),
                str(item.payload.get("direction_type") or ""),
            )
            if value
        }
        if direction_id not in known_directions:
            raise ContentResearchNotFoundError(
                f"Content research direction not found: {direction_id}"
            )

        packet_read = PacketEvidenceReader(self._store).read_direction(
            workflow_run_id=workflow_run_id,
            direction_id=direction_id,
            offset=offset,
            limit=limit,
        )
        checkpoints = packet_read.checkpoints
        collect = next(
            (item for item in reversed(checkpoints) if item.stage_name == "collect"), None
        )
        selection_checkpoint = next(
            (item for item in reversed(checkpoints) if item.stage_name == "selection"), None
        )
        detail_checkpoint = next(
            (item for item in reversed(checkpoints) if item.stage_name == "detail"), None
        )
        packet_checkpoint = next(
            (item for item in reversed(checkpoints) if item.stage_name == "packet"), None
        )
        comment_checkpoint = next(
            (item for item in reversed(checkpoints) if item.stage_name == "comments"), None
        )
        selection_revisions = [
            item
            for item in checkpoints
            if item.stage_name == "selection_revision"
            and item.payload.get("base_selection_fingerprint")
            == (selection_checkpoint.input_fingerprint if selection_checkpoint else None)
        ]
        selection = (
            (detail_checkpoint.payload.get("selection") if detail_checkpoint else None)
            or (selection_checkpoint.payload.get("selection") if selection_checkpoint else {})
            or {}
        )
        decisions = list(selection.get("decisions") or [])
        candidates = list((collect.payload.get("candidates") if collect else []) or [])
        selected = [item for item in decisions if item.get("selected")]
        excluded = [item for item in decisions if not item.get("selected")]
        packet_views = [
            safe_read_model(
                {
                    "packet_id": item.id,
                    "canonical_source_id": item.canonical_source_id,
                    **item.payload,
                }
            )
            for item in packet_read.packets
        ]
        projection_by_packet = {
            item.evidence_packet_id: item for item in packet_read.projections
        }
        for packet in packet_views:
            projection = projection_by_packet.get(packet["packet_id"])
            if projection:
                packet["selection"] = safe_read_model(projection.payload)
        candidate_ids = {
            item.id for item in self._store.list_claim_candidates(workflow_run_id, direction_id)
        }
        admission_ids = {
            item.id
            for item in self._store.list_typed_records(ClaimAdmissionDecisionRecord)
            if item.claim_candidate_id in candidate_ids
        }
        snapshot = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        direction_result = next(
            (
                item.payload
                for item in reversed(self._store.list_typed_records(DirectionResultDecisionRecord))
                if item.research_direction_id == direction_id
                and snapshot is not None
                and item.policy_snapshot_id == snapshot.id
            ),
            {},
        )
        weak_signals = [
            item.payload
            for item in self._store.list_typed_records(WeakSignalRecord)
            if item.admission_decision_id in admission_ids
        ]
        return ContentResearchDirectionEvidenceResponse(
            workflow_run_id=workflow_run_id,
            direction_id=direction_id,
            status=str(
                (packet_checkpoint.payload.get("status") if packet_checkpoint else None)
                or selection.get("status")
                or "not_started"
            ),
            counts={
                "selected_source_count": int(selection.get("selected_source_count") or 0),
                "eligible_source_count": int(selection.get("eligible_source_count") or 0),
                "independent_source_count": self._store.count_run_independent_sources(
                    workflow_run_id
                ),
            },
            query_plan_hash=selection.get("query_plan_hash"),
            candidate_manifest_hash=selection.get("candidate_manifest_hash"),
            query_groups=list((collect.payload.get("query_groups") if collect else []) or []),
            selection_policy=dict(
                (
                    selection_checkpoint.payload.get("selection_policy")
                    if selection_checkpoint
                    else collect.payload.get("selection_policy")
                    if collect
                    else {}
                )
                or {}
            ),
            coverage_unmet_query_group_ids=list(
                selection.get("coverage_unmet_query_group_ids") or []
            ),
            selection_revisions=[
                safe_read_model(
                    {key: value for key, value in item.payload.items() if key != "candidates"}
                )
                for item in selection_revisions
            ],
            comment_collection=safe_read_model(
                {
                    key: value
                    for key, value in (
                        comment_checkpoint.payload if comment_checkpoint else {}
                    ).items()
                    if key != "packet_ids"
                }
            ),
            candidates=[safe_read_model(item) for item in candidates[offset : offset + limit]],
            selections=[safe_read_model(item) for item in selected[offset : offset + limit]],
            exclusions=[safe_read_model(item) for item in excluded[offset : offset + limit]],
            packets=packet_views,
            direction_result=direction_result,
            weak_signals=weak_signals,
            offset=offset,
            limit=limit,
        )
