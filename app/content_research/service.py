"""Content Research application service."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from typing import Any, Protocol

import aiosqlite

from app.content_research.admission.cross_direction import (
    ActionHypothesisRequest,
    CrossDirectionGovernanceService,
)
from app.content_research.advancement import DecisionAdvancementService
from app.content_research.analysis import DirectionalAnalysisLLM
from app.content_research.api_schemas import (
    CONTENT_RESEARCH_API_SCHEMA_VERSION,
    P0_WORKFLOW_ACTIONS,
    ContentResearchBriefConfirmationRequest,
    ContentResearchBriefResponse,
    ContentResearchDirectionEvidenceResponse,
    ContentResearchDirectionResponse,
    ContentResearchFormalResearchResponse,
    ContentResearchGovernanceResponse,
    ContentResearchHistoricalWorkflowSummaryResponse,
    ContentResearchLiteReportResponse,
    ContentResearchPlanResponse,
    ContentResearchPresearchResponse,
    ContentResearchRunProjectionResponse,
    ContentResearchScopeProjectionResponse,
    ContentResearchSourceCollectionRequest,
    ContentResearchSubagentTaskResponse,
    ContentResearchSubjectRevisionRequest,
    ContentResearchTraceResponse,
    ContentResearchWorkflowActionRequest,
    ContentResearchWorkflowActionResponse,
    ContentResearchWorkflowEventsResponse,
    ContentResearchWorkflowSummaryResponse,
    HumanDecisionRequest,
    HumanDecisionResponse,
    HumanDecisionsResponse,
    ReplaceScopeDraftRequest,
    ResolveCoverageRequest,
    SnapshotResponse,
)
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.contracts import (
    DIRECTION_CATALOG_V1,
    build_default_snapshot,
)
from app.content_research.decisions import ResearchDecisionService
from app.content_research.evidence import EvidenceService
from app.content_research.evidence.governance_reader import (
    GovernanceReadModelReader,
    safe_public_projection,
)
from app.content_research.evidence.packet_reader import PacketEvidenceReader
from app.content_research.execution_lease import (
    DispatchLeaseFencedWorkflowRunManager,
    LeaseFencedWorkflowRunManager,
)
from app.content_research.marketing_conclusion_analysis import (
    MarketingConclusionAnalysisError,
    MarketingConclusionAnalysisService,
)
from app.content_research.marketing_conclusions import evaluate_marketing_conclusions
from app.content_research.lifecycle.coordinator import (
    ContentResearchPersistenceCoordinator,
    LifecycleCommandConflict,
    LifecyclePersistenceBusy,
)
from app.content_research.lifecycle.models import (
    ContentResearchState,
    LifecycleCommand,
    RunProjection,
)
from app.content_research.models import (
    ObservationEventRecord,
    ResearchBriefRecord,
    ResearchDirectionRecord,
    ResearchPlanRecord,
    ResearchResultSnapshotRecord,
    SubagentTaskRecord,
    TraceRecord,
    utcnow,
)
from app.content_research.observation import ContentResearchTraceService
from app.content_research.persisted_packet_replay import PersistedPacketReplayInput
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CoverageManifest,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
    MarketingConclusionDecisionRecord,
    ReportDraftRecord,
    ReportFaithfulnessDecisionRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
    WeakSignalRecord,
)
from app.content_research.presearch.service import (
    PresearchInput,
    PresearchOutcome,
    PresearchService,
)
from app.content_research.reporting.execution import ReportExecutionService
from app.content_research.reporting.faithfulness import (
    LLMReportSemanticAuditor,
    ReportSemanticAuditor,
    UnavailableReportSemanticAuditor,
)
from app.content_research.reporting.lite_read_model import LiteReportReader
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.reporting.read_model import (
    ExecutionTraceReader,
    PublishedReportNotFoundError,
)
from app.content_research.runtime import canonical_fingerprint
from app.content_research.scope_contract import (
    SCOPE_CONTRACT_SCHEMA_VERSION_V2,
    CoverageSnapshot,
    DispatchLeaseContext,
    ExecutionContext,
    ExecutionLeaseFencedError,
    ResearchScopeDraft,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeDraftAuditEvent,
    ScopeExecutionAttempt,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeExecutionUnit,
    ScopeQueryGroupInput,
    build_scope_contract,
    build_scope_draft,
)
from app.content_research.sources import (
    SourceAdapterRegistry,
)
from app.content_research.sources.base import (
    CollectCommentsRequest,
    CollectNoteDetailRequest,
    DiscoverCandidatesRequest,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.subject_structure import (
    parse_subject_structure,
    subject_structure_fingerprint,
    subject_structure_payload,
)
from app.content_research.workflow import (
    BriefConfirmation,
    ResearchDirectionRegistry,
    ResearchPlanBuilder,
    SubagentTaskRouter,
)
from app.content_research.workflow.directional_pipeline import (
    DirectionalExecutionPipeline,
    persist_scope_coverage_evaluation,
)
from app.content_research.workflow.query_planner import (
    QUERY_COMPILER_VERSION,
    CompiledQueryPlan,
    compile_product_marketing_query_plan,
    compile_product_marketing_query_portfolio,
    compile_structured_query_plan,
    concrete_product_marketing_aspect,
)
from app.content_research.workflow_mutation_authority import (
    LegacyRecoveryActionUnavailableError,
    LegacyRecoveryAuthority,
    legacy_recovery_ownership_unavailable,
    project_legacy_recovery_authority,
)
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase
from app.services.llm.failures import LLMProviderFailure
from app.services.workflow_run_manager import WorkflowRunManager


class ContentResearchError(ValueError):
    """Base error for Content Research service failures."""


class ContentResearchNotFoundError(ContentResearchError):
    """Raised when a requested Content Research object is missing."""


class ContentResearchValidationError(ContentResearchError):
    """Raised when a request payload is invalid."""


class ContentResearchStateConflictError(ContentResearchValidationError):
    """Raised when a valid action is unsafe for the current durable state."""

    def __init__(self, message: str, *, error_code: str, suggested_action: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.suggested_action = suggested_action


class ContentResearchReportIntegrityError(RuntimeError):
    """Raised when an existing published report cannot be safely projected."""


class ReportPublicationMaterializationError(RuntimeError):
    """Carry the exact persisted publication across the materialization boundary."""

    def __init__(self, publication_id: str, cause: Exception) -> None:
        super().__init__(str(cause) or "Report publication failed.")
        self.publication_id = publication_id


class WorkflowRuntime(Protocol):

    async def record_step_execution_started(self, workflow_run_id: str, step_name: str) -> None: ...

    async def record_step_execution_finished(
        self, workflow_run_id: str, step_name: str
    ) -> None: ...

    async def abort_step_execution(self, workflow_run_id: str, step_name: str) -> None: ...

    async def complete_brief_and_plan_atomically(
        self,
        *,
        workflow_run_id: str,
        task_specs: list[dict],
        confirmation_writer: Callable[[aiosqlite.Connection, list[str]], Awaitable[None]],
    ) -> list[str]: ...

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict: ...

    async def list_events(self, workflow_run_id: str) -> list[dict]: ...

    async def append_event(
        self,
        *,
        workflow_run_id: str,
        thread_id: str,
        event_type: str,
        payload: dict,
    ) -> None: ...

    async def end_content_research_run(self, *, workflow_run_id: str, thread_id: str) -> dict: ...

    async def pause_content_research_run(self, *, workflow_run_id: str) -> dict: ...

    async def resume_content_research_run(self, *, workflow_run_id: str) -> dict: ...

    async def restart_formal_research_step(
        self,
        *,
        workflow_run_id: str,
        child_task_ids: list[str],
        resume_parent: bool = True,
    ) -> dict: ...

    async def acknowledge_pause_at_safe_boundary(self, *, workflow_run_id: str) -> dict: ...

    async def complete_formal_research(
        self, *, workflow_run_id: str, task_outcomes: list[dict], artifact_refs: list[dict]
    ) -> bool: ...

    async def complete_report_publication(self, *, workflow_run_id: str) -> bool: ...

    async def retry_failed_report_publication(
        self, *, workflow_run_id: str, publication_id: str
    ) -> bool: ...

    async def wait_for_user_recovery(self, *, workflow_run_id: str, reason: dict) -> dict: ...

    async def fail_formal_research(self, *, workflow_run_id: str, reason: dict) -> dict: ...


class WorkflowRunManagerRuntime:
    def __init__(
        self,
        db_path: str,
        *,
        execution_context: ExecutionContext | None = None,
        dispatch_context: DispatchLeaseContext | None = None,
    ) -> None:
        self._db_path = db_path
        self._execution_context = execution_context
        self._dispatch_context = dispatch_context

    def for_execution_context(self, context: ExecutionContext) -> WorkflowRunManagerRuntime:
        return WorkflowRunManagerRuntime(self._db_path, execution_context=context)

    def for_dispatch_context(
        self, context: DispatchLeaseContext
    ) -> WorkflowRunManagerRuntime:
        return WorkflowRunManagerRuntime(self._db_path, dispatch_context=context)

    def _manager(self, operation: str) -> WorkflowRunManager:
        if self._execution_context is None:
            if self._dispatch_context is None:
                return WorkflowRunManager(self._db_path)
            return DispatchLeaseFencedWorkflowRunManager(
                self._db_path,
                dispatch_context=self._dispatch_context,
                operation=operation,
            )
        return LeaseFencedWorkflowRunManager(
            self._db_path,
            execution_context=self._execution_context,
            operation=operation,
        )


    async def record_step_execution_started(self, workflow_run_id: str, step_name: str) -> None:
        async with self._manager("record_step_execution_started") as manager:
            await manager.record_step_execution_started(workflow_run_id, step_name)

    async def record_step_execution_finished(self, workflow_run_id: str, step_name: str) -> None:
        async with self._manager("record_step_execution_finished") as manager:
            await manager.record_step_execution_finished(workflow_run_id, step_name)

    async def abort_step_execution(self, workflow_run_id: str, step_name: str) -> None:
        async with self._manager("abort_step_execution") as manager:
            await manager.abort_step_execution(workflow_run_id, step_name)

    async def complete_brief_and_plan_atomically(
        self,
        *,
        workflow_run_id: str,
        task_specs: list[dict],
        confirmation_writer: Callable[[aiosqlite.Connection, list[str]], Awaitable[None]],
    ) -> list[str]:
        async with WorkflowRunManager(self._db_path) as manager:
            return await manager.complete_brief_and_plan_atomically(
                workflow_run_id=workflow_run_id,
                task_specs=task_specs,
                confirmation_writer=confirmation_writer,
            )

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        async with WorkflowStore(self._db_path) as store:
            run = await store.get_run(workflow_run_id)
            steps = await store.list_steps(workflow_run_id)
            child_tasks = await store.list_child_tasks(workflow_run_id)
        return {
            "run": run.model_dump(mode="json") if run else None,
            "steps": [step.model_dump(mode="json") for step in steps],
            "child_tasks": [task.model_dump(mode="json") for task in child_tasks],
        }

    async def complete_formal_research(
        self, *, workflow_run_id: str, task_outcomes: list[dict], artifact_refs: list[dict]
    ) -> bool:
        snapshot = await self.get_runtime_snapshot(workflow_run_id)
        # A successful formal run is immutable.  Retrying its public action is
        # a safe replay request, not permission to complete the runtime a
        # second time (which the workflow manager correctly rejects).
        if str((snapshot.get("run") or {}).get("status") or "") in {
            "finalizing_report",
            "succeeded",
        }:
            return True
        child_by_id = {str(task["child_task_id"]): task for task in snapshot["child_tasks"]}
        expected_child_ids = set(child_by_id)
        outcome_child_ids = {str(outcome["child_task_id"]) for outcome in task_outcomes}
        missing_child_ids = expected_child_ids - outcome_child_ids
        if missing_child_ids:
            raise ValueError(
                "Cannot complete formal research before every child task has a terminal outcome: "
                + ", ".join(sorted(missing_child_ids))
            )
        async with self._manager("complete_formal_research") as manager:
            for outcome in task_outcomes:
                child_id = str(outcome["child_task_id"])
                child_status = str(child_by_id[child_id].get("status") or "pending")
                succeeded = outcome["status"] in {"completed", "partial_completed"}
                if child_status == "succeeded" and succeeded:
                    continue
                if child_status == "failed" and succeeded:
                    await manager.retry_child_task(
                        child_id, "retrying failed content research subagent"
                    )
                    child_status = "retrying"
                if child_status in {"pending", "retrying"}:
                    await manager.start_child_task(child_id)
                if succeeded:
                    await manager.complete_child_task(
                        child_id, artifact_refs=outcome.get("artifact_refs") or []
                    )
                elif child_status != "failed":
                    await manager.fail_child_task(
                        child_id, outcome.get("error") or "subagent execution failed"
                    )
            if any(outcome["status"] == "failed" for outcome in task_outcomes):
                # A failed specialist is visible and retryable, but it cannot
                # silently advance the parent step or expose decision UI.
                return False
            await manager.complete_step(
                workflow_run_id, "formal_research", artifact_refs=artifact_refs
            )
            await manager.begin_report_finalization(workflow_run_id)
        return True

    async def complete_report_publication(self, *, workflow_run_id: str) -> bool:
        async with self._manager("complete_report_publication") as manager:
            await manager.complete_report_finalization(workflow_run_id)
        return True

    async def retry_failed_report_publication(
        self, *, workflow_run_id: str, publication_id: str
    ) -> bool:
        async with self._manager("retry_failed_report_publication") as manager:
            await manager.retry_failed_report_finalization(
                workflow_run_id, publication_id=publication_id
            )
        return True

    async def wait_for_user_recovery(
        self,
        *,
        workflow_run_id: str,
        reason: dict,
    ) -> dict:
        async with self._manager("wait_for_user_recovery") as manager:
            run = await manager.wait_for_user_recovery(
                workflow_run_id,
                step_name="formal_research",
                reason=reason,
            )
        return {"workflow_run_id": workflow_run_id, "status": run.status.value, "recoverable": True}

    async def fail_formal_research(
        self,
        *,
        workflow_run_id: str,
        reason: dict,
    ) -> dict:
        async with WorkflowStore(self._db_path) as store:
            current_run = await store.get_run(workflow_run_id)
        async with self._manager("fail_formal_research") as manager:
            # Report composition begins only after formal_research has already
            # completed.  A finalization error must fail the parent directly;
            # trying to fail that completed step would leave the run stuck.
            if current_run is None or current_run.status.value != "finalizing_report":
                await manager.fail_step(workflow_run_id, "formal_research", reason)
            run = await manager.fail_run(workflow_run_id, reason)
        return {
            "workflow_run_id": workflow_run_id,
            "status": run.status.value,
            "recoverable": False,
        }

    async def list_events(self, workflow_run_id: str) -> list[dict]:
        async with WorkflowStore(self._db_path) as store:
            events = await store.list_events(workflow_run_id)
        return [event.model_dump(mode="json") for event in events]

    async def append_event(
        self,
        *,
        workflow_run_id: str,
        thread_id: str,
        event_type: str,
        payload: dict,
    ) -> None:
        async with self._manager("append_workflow_event") as manager:
            await manager.append_event(
                run_id=workflow_run_id,
                event_type=event_type,
                payload=payload,
            )

    async def end_content_research_run(self, *, workflow_run_id: str, thread_id: str) -> dict:
        cancel_status = "not_cancelled"
        async with WorkflowStore(self._db_path) as store:
            run = await store.get_run(workflow_run_id)
        status_value = run.status.value if run is not None else ""
        if status_value in {"running", "pausing", "paused", "finalizing_report"}:
            async with WorkflowRunManager(self._db_path) as manager:
                cancelled = await manager.cancel_run(
                    workflow_run_id, reason="content_research_ended"
                )
                cancel_status = cancelled.status.value
        elif run is not None:
            cancel_status = status_value

        async with ThreadStore(self._db_path) as thread_store:
            await thread_store.update_thread_active_run(thread_id, None)

        await self.append_event(
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            event_type="content_research_archived",
            payload={
                "schema_version": "content_research_workflow_event_v1",
                "reason": "user_archived_content_research",
                "active_run_cleared": True,
                "cancel_status": cancel_status,
            },
        )
        return {
            "schema_version": CONTENT_RESEARCH_API_SCHEMA_VERSION,
            "ended": True,
            "archived": True,
            "workflow_run_id": workflow_run_id,
            "thread_id": thread_id,
            "active_run_cleared": True,
            # Archiving keeps every research record available for audit and
            # recovery while allowing a new run in the same Creator thread.
            "resources_destroyed": False,
            "cancel_status": cancel_status,
        }

    async def pause_content_research_run(self, *, workflow_run_id: str) -> dict:
        async with self._manager("pause_content_research_run") as manager:
            run = await manager.pause_run(workflow_run_id, reason="content_research_user_pause")
        return {"workflow_run_id": workflow_run_id, "status": run.status.value, "recoverable": True}

    async def resume_content_research_run(self, *, workflow_run_id: str) -> dict:
        async with self._manager("resume_content_research_run") as manager:
            run = await manager.resume_run(workflow_run_id)
        return {"workflow_run_id": workflow_run_id, "status": run.status.value, "recoverable": True}

    async def restart_formal_research_step(
        self,
        *,
        workflow_run_id: str,
        child_task_ids: list[str],
        resume_parent: bool = True,
    ) -> dict:
        """Resume a waiting Content Research run and restart its retryable parent step."""
        async with self._manager("restart_formal_research_step") as manager:
            snapshot = await self.get_runtime_snapshot(workflow_run_id)
            run_status = str((snapshot.get("run") or {}).get("status") or "")
            formal_step = next(
                (
                    step
                    for step in snapshot.get("steps") or []
                    if step.get("step_name") == "formal_research"
                ),
                {},
            )
            formal_status = str(formal_step.get("status") or "")
            if run_status == "running" and formal_status == "retrying":
                # Legacy v23 scope decisions can leave the parent running while
                # formal_research is retrying.  This is already a resumed run;
                # only start the retrying step, rather than calling resume_run
                # (which is intentionally invalid for ordinary running runs).
                await manager.start_step(workflow_run_id, "formal_research")
                run = None
                recovered_children = []
            elif run_status == "running" and formal_status == "running":
                # A reclaimed continuation may observe its own prior restart.
                # Treat that state as an idempotent claim, without reopening it.
                run = None
                recovered_children = []
            else:
                run, recovered_children = await manager.restart_step_and_retry_children(
                    workflow_run_id,
                    step_name="formal_research",
                    child_task_ids=child_task_ids,
                    resume_parent=resume_parent,
                )
            run_status = run.status.value if run is not None else "running"
        return {
            "workflow_run_id": workflow_run_id,
            "status": run_status,
            "recoverable": True,
            "recovered_child_task_ids": [child.child_task_id for child in recovered_children],
        }

    async def acknowledge_pause_at_safe_boundary(self, *, workflow_run_id: str) -> dict:
        async with self._manager("acknowledge_pause_at_safe_boundary") as manager:
            run = await manager.ack_pause_at_boundary(workflow_run_id, "formal_research")
        return {"workflow_run_id": workflow_run_id, "status": run.status.value, "recoverable": True}


class ContentResearchService:
    def __init__(
        self,
        *,
        store: SQLiteContentResearchStore,
        presearch: PresearchService,
        workflow_runtime: WorkflowRuntime,
        source_registry: SourceAdapterRegistry | None = None,
        analysis_llm: DirectionalAnalysisLLM | None = None,
        report_semantic_auditor: ReportSemanticAuditor | None = None,
        dispatch_wake_event: asyncio.Event | None = None,
    ) -> None:
        self._store = store
        self._lifecycle = ContentResearchPersistenceCoordinator(store._db_path)
        self._presearch = presearch
        self._workflow_runtime = workflow_runtime
        self._direction_registry = ResearchDirectionRegistry()
        self._plan_builder = ResearchPlanBuilder()
        self._trace_service = ContentResearchTraceService(store=store, db_path=store._db_path)
        self._source_registry = source_registry or SourceAdapterRegistry()
        self._evidence_service = EvidenceService(store)
        self._task_router = SubagentTaskRouter(store=store, source_registry=self._source_registry)
        self._decision_service = ResearchDecisionService(
            store=store, workflow_runtime=workflow_runtime
        )
        self._decision_advancement_service = DecisionAdvancementService(store=store)
        self._cross_direction_governance = CrossDirectionGovernanceService(store)
        self._report_execution = ReportExecutionService(store)
        self._analysis_llm = analysis_llm
        self._dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
        self._dispatch_wake_event = dispatch_wake_event
        self._recovery_locks: dict[str, asyncio.Lock] = {}
        self._presearch_command_locks: dict[str, asyncio.Lock] = {}
        self._lifecycle_reconciliation_tasks: set[asyncio.Task[None]] = set()
        # A configured analysis LLM also supplies the bounded report reviewer.
        # Without one, publication remains safely non-complete.
        self._report_semantic_auditor = report_semantic_auditor or (
            LLMReportSemanticAuditor(analysis_llm)
            if analysis_llm is not None
            else UnavailableReportSemanticAuditor()
        )

    async def reconcile_startup(self) -> list[RunProjection]:
        """Converge lifecycle work interrupted by the previous process."""

        return await self._lifecycle.reconcile_interrupted_presearch()

    async def submit_presearch(
        self,
        *,
        command_id: str,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        user_id: str,
        workspace_id: str = "default",
    ) -> ContentResearchPresearchResponse:
        normalized_command_id = command_id.strip()
        if not normalized_command_id:
            raise ContentResearchValidationError("command_id is required")
        lock = self._presearch_command_locks.setdefault(
            normalized_command_id, asyncio.Lock()
        )
        async with lock:
            return await self._submit_presearch_locked(
                command_id=normalized_command_id,
                seed_text=seed_text,
                user_note=user_note,
                thread_id=thread_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )

    async def _submit_presearch_locked(
        self,
        *,
        command_id: str,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        user_id: str,
        workspace_id: str,
    ) -> ContentResearchPresearchResponse:
        normalized_seed = seed_text.strip()
        if not normalized_seed:
            raise ContentResearchValidationError("seed_text is required")

        workflow_run_id = self._stable_id("run", command_id)
        submitted = await self._lifecycle.apply(
            LifecycleCommand(
                command_id=command_id,
                run_id=workflow_run_id,
                expected_state=None,
                expected_revision=0,
                kind="submit_research_subject",
                payload={
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "seed_text": normalized_seed,
                    "user_note": user_note,
                    "workspace_id": workspace_id,
                },
            )
        )
        if submitted.brief_id is not None:
            existing_brief = self._store.get_brief(submitted.brief_id)
            if existing_brief is None:
                raise ContentResearchValidationError(
                    "lifecycle projection references a missing Brief"
                )
            return self._response_from_brief(
                existing_brief,
                run_projection=submitted,
            )
        attempt_id = self._stable_id("att", f"{command_id}:attempt")
        brief_id = self._stable_id("rb", f"{command_id}:brief")
        request = PresearchInput(
            seed_text=normalized_seed,
            user_note=user_note,
            thread_id=thread_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        llm_task = await self._presearch.create_llm_task(request)
        outcome = await self._presearch.wait_for_first_feedback(request=request, task=llm_task)
        if llm_task is not None and outcome.timeout_status == "first_timeout":
            settled = await self._presearch.wait_for_hard_cutoff(request=request, task=llm_task)
            if settled is not None:
                outcome = settled
        brief_payload = {
            "brief_id": brief_id,
            "schema_version": "content_research_brief_v1",
            "brief_status": "draft" if outcome.status == "completed" else "failed",
            "subject": outcome.checklist.subject_confirmation or normalized_seed,
            "competitors": list(outcome.checklist.competitor_tags),
            "directions": list(outcome.checklist.research_directions) or ["product_marketing"],
            "attempt_id": attempt_id,
            "seed_text": normalized_seed,
            "user_note": user_note,
            "workspace_id": workspace_id,
            "user_id": user_id,
            **self._outcome_payload(outcome),
        }
        run_projection = await self._commit_presearch_outcome(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            expected_revision=1,
            brief_payload=brief_payload,
            outcome=outcome,
        )
        brief = self._store.get_brief(brief_id)
        assert brief is not None
        return self._response_from_brief(brief, run_projection=run_projection)

    def get_presearch(self, attempt_id: str) -> ContentResearchPresearchResponse:
        brief = self._store.get_brief_by_presearch_attempt(attempt_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Presearch attempt not found: {attempt_id}")
        return self._response_from_brief(
            brief,
            run_projection=self._lifecycle.load_now(brief.workflow_run_id),
        )

    async def retry_presearch(
        self,
        workflow_run_id: str,
        *,
        command_id: str,
        expected_state: ContentResearchState,
        expected_revision: int,
    ) -> ContentResearchPresearchResponse:
        return await self._rerun_presearch(
            workflow_run_id=workflow_run_id,
            event="retry_presearch",
            expected_state=expected_state,
            expected_revision=expected_revision,
            command_id=command_id,
            clarification_text=None,
        )

    async def revise_subject(
        self,
        *,
        workflow_run_id: str,
        command_id: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        clarification_text: str,
    ) -> ContentResearchPresearchResponse:
        clarification = clarification_text.strip()
        if not clarification:
            raise ContentResearchValidationError("clarification_text is required")
        return await self._rerun_presearch(
            workflow_run_id=workflow_run_id,
            event="revise_subject",
            expected_state=expected_state,
            expected_revision=expected_revision,
            command_id=command_id,
            clarification_text=clarification,
        )

    async def _rerun_presearch(
        self,
        *,
        workflow_run_id: str,
        event: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        command_id: str,
        clarification_text: str | None,
    ) -> ContentResearchPresearchResponse:
        normalized_command_id = command_id.strip()
        if not normalized_command_id:
            raise ContentResearchValidationError("command_id is required")
        lock = self._presearch_command_locks.setdefault(
            normalized_command_id, asyncio.Lock()
        )
        async with lock:
            return await self._rerun_presearch_locked(
                workflow_run_id=workflow_run_id,
                event=event,
                expected_state=expected_state,
                expected_revision=expected_revision,
                command_id=normalized_command_id,
                clarification_text=clarification_text,
            )

    async def _rerun_presearch_locked(
        self,
        *,
        workflow_run_id: str,
        event: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        command_id: str,
        clarification_text: str | None,
    ) -> ContentResearchPresearchResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        started = await self._lifecycle.apply(
            LifecycleCommand(
                command_id=command_id,
                run_id=workflow_run_id,
                expected_state=expected_state,
                expected_revision=expected_revision,
                kind=event,
                payload={"clarification_text": clarification_text},
            )
        )
        if started.state is not ContentResearchState.PRESEARCH_RUNNING:
            existing_brief = self._store.get_brief_by_workflow(workflow_run_id)
            if existing_brief is None:
                raise ContentResearchValidationError(
                    "lifecycle projection references a missing Brief"
                )
            return self._response_from_brief(
                existing_brief,
                run_projection=started,
            )
        previous = brief.payload
        prior_clarifications = [
            str(item).strip()
            for item in list(previous.get("subject_clarifications") or [])
            if str(item).strip()
        ]
        if clarification_text:
            prior_clarifications.append(clarification_text)
        original_note = str(previous.get("user_note") or "").strip()
        accumulated_note = "\n".join(
            item for item in [original_note, *prior_clarifications] if item
        )
        attempt_id = self._stable_id("att", f"{command_id}:attempt")
        request = PresearchInput(
            seed_text=str(previous["seed_text"]),
            user_note=accumulated_note or None,
            thread_id=brief.thread_id,
            workflow_run_id=workflow_run_id,
            user_id=str(previous.get("user_id") or "default"),
            workspace_id=str(previous.get("workspace_id") or "default"),
        )
        llm_task = await self._presearch.create_llm_task(request)
        outcome = await self._presearch.wait_for_first_feedback(
            request=request,
            task=llm_task,
        )
        if llm_task is not None and outcome.timeout_status == "first_timeout":
            settled = await self._presearch.wait_for_hard_cutoff(
                request=request,
                task=llm_task,
            )
            if settled is not None:
                outcome = settled
        brief_payload = {
            "brief_id": brief.id,
            "schema_version": "content_research_brief_v1",
            "brief_status": "draft" if outcome.status == "completed" else "failed",
            "subject": outcome.checklist.subject_confirmation or request.seed_text,
            "competitors": list(outcome.checklist.competitor_tags),
            "directions": list(outcome.checklist.research_directions)
            or ["product_marketing"],
            "attempt_id": attempt_id,
            "seed_text": request.seed_text,
            "user_note": request.user_note,
            "workspace_id": request.workspace_id,
            "user_id": request.user_id,
            "subject_clarifications": prior_clarifications,
            **self._outcome_payload(outcome),
        }
        projection = await self._commit_presearch_outcome(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            expected_revision=started.state_revision,
            brief_payload=brief_payload,
            outcome=outcome,
        )
        updated = self._store.get_brief(brief.id)
        assert updated is not None
        return self._response_from_brief(updated, run_projection=projection)

    async def _commit_presearch_outcome(
        self,
        *,
        command_id: str,
        workflow_run_id: str,
        expected_revision: int,
        brief_payload: dict[str, Any],
        outcome: PresearchOutcome,
    ) -> RunProjection:
        error = {
            "code": outcome.error_code or "PRESEARCH_FAILED",
            "stage": "presearch",
            "operation": "llm_presearch",
            "message": outcome.error_message or "轻量预检索未能完成。",
            "retryable": bool(outcome.recoverable),
            "recovery_action": "retry_presearch",
        }
        try:
            return await self._lifecycle.apply(
                LifecycleCommand(
                    command_id=f"{command_id}:presearch-outcome",
                    run_id=workflow_run_id,
                    expected_state=ContentResearchState.PRESEARCH_RUNNING,
                    expected_revision=expected_revision,
                    kind=(
                        "presearch_completed"
                        if outcome.status == "completed"
                        else "fail"
                    ),
                    payload=(
                        brief_payload
                        if outcome.status == "completed"
                        else {**brief_payload, "error": error}
                    ),
                )
            )
        except LifecyclePersistenceBusy:
            persistence_error = {
                "code": "LOCAL_PERSISTENCE_BUSY",
                "stage": "presearch",
                "operation": "persist_presearch_outcome",
                "message": "本地数据写入暂时繁忙，自动重试未成功。",
                "retryable": True,
                "automatic_attempts": 3,
                "recovery_action": "retry_presearch",
            }
            recovery_command = LifecycleCommand(
                command_id=f"{command_id}:persistence-failure",
                run_id=workflow_run_id,
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=expected_revision,
                kind="fail",
                payload={
                    **brief_payload,
                    "brief_status": "failed",
                    "status": "failed",
                    "error_code": persistence_error["code"],
                    "error_message": persistence_error["message"],
                    "recoverable": True,
                    "error": persistence_error,
                },
            )
            try:
                current = await self._lifecycle.load(workflow_run_id)
            except LifecyclePersistenceBusy:
                self._schedule_lifecycle_reconciliation(recovery_command)
                raise
            if current.state is not ContentResearchState.PRESEARCH_RUNNING:
                return current
            try:
                return await self._lifecycle.apply(recovery_command)
            except LifecyclePersistenceBusy:
                self._schedule_lifecycle_reconciliation(recovery_command)
                raise

    def _schedule_lifecycle_reconciliation(self, command: LifecycleCommand) -> None:
        """Converge after SQLite becomes writable; startup covers process exit."""

        async def reconcile() -> None:
            delay = 0.1
            while True:
                try:
                    current = await self._lifecycle.load(command.run_id)
                    if current.state is not command.expected_state:
                        return
                    await self._lifecycle.apply(command)
                    return
                except LifecyclePersistenceBusy:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2.0)
                except LifecycleCommandConflict:
                    return

        task = asyncio.create_task(reconcile())
        self._lifecycle_reconciliation_tasks.add(task)
        task.add_done_callback(self._lifecycle_reconciliation_tasks.discard)


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
        runtime_snapshot = await self._workflow_runtime.get_runtime_snapshot(workflow_run_id)
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
                runtime_run=runtime_snapshot.get("run"),
                runtime_steps=list(runtime_snapshot.get("steps") or []),
                runtime_child_tasks=list(runtime_snapshot.get("child_tasks") or []),
            )
        return ContentResearchWorkflowSummaryResponse(
            workflow_run_id=workflow_run_id,
            run=self._run_projection_payload(run_projection),
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
            runtime_run=runtime_snapshot.get("run"),
            runtime_steps=list(runtime_snapshot.get("steps") or []),
            runtime_child_tasks=list(runtime_snapshot.get("child_tasks") or []),
            local_cache_id=brief.id if brief is not None else None,
        )

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
            _scope_draft_audit_payload(event)
            for event in self._store.list_scope_draft_audit_events(
                workflow_run_id, scope_draft_id=draft.id
            )
        ]
        if contract is not None:
            audit_events.extend(
                _scope_audit_payload(event)
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
                        _scope_query_input_payload(item) for item in draft.query_groups
                    ],
                }
            ]
            if run_projection.state is ContentResearchState.SCOPE_CONFIRMATION_REQUIRED
            and "replace_scope_draft" in run_projection.allowed_actions
            else []
        )
        allowed_resolutions = (
            _scope_projection_resolutions(
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
            self._store.execution_trace(execution_unit.id)
            if execution_unit is not None
            else []
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
                **self._run_projection_payload(run_projection)
            ),
            draft=_scope_draft_payload(draft),
            scope_contract=_scope_contract_payload(contract) if contract is not None else None,
            audit_events=sorted(
                (safe_public_projection(event) for event in audit_events),
                key=lambda event: (str(event["created_at"]), str(event["id"])),
            ),
            allowed_actions=allowed_actions,
            coverage_snapshot=(
                _coverage_snapshot_payload(coverage_snapshot)
                if coverage_snapshot is not None
                else None
            ),
            allowed_resolutions=allowed_resolutions,
            decision_recovery=_scope_decision_recovery(
                coverage_snapshot=coverage_snapshot,
                authorizations=authorizations,
                allowed_resolutions=allowed_resolutions,
            ) if run_projection.state is ContentResearchState.COVERAGE_DECISION_REQUIRED else None,
            execution_unit=_scope_execution_unit_projection(
                execution_unit=execution_unit,
                authorization=current_authorization,
                audit_events=audit_events,
                execution_facts=execution_facts,
            ),
        )

    async def get_workflow_trace(self, workflow_run_id: str) -> ContentResearchTraceResponse:
        try:
            run = await self._lifecycle.load(workflow_run_id)
        except LifecycleCommandConflict as exc:
            if str(exc) != "Run does not exist":
                raise
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            ) from exc
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        trace = await self._trace_service.build_trace(
            workflow_run_id=workflow_run_id,
            brief=brief,
        )
        transitions = await self._lifecycle.list_transitions(workflow_run_id)
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
        status_by_state = {
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED: "waiting_user",
            ContentResearchState.SCOPE_CONFIRMATION_REQUIRED: "waiting_user",
            ContentResearchState.COVERAGE_DECISION_REQUIRED: "waiting_user",
            ContentResearchState.RECOVERY_REQUIRED: "waiting_user",
            ContentResearchState.REPORT_READY: "succeeded",
            ContentResearchState.CANCELLED_OR_FAILED: "failed",
        }
        run_status = status_by_state.get(run.state, "running")
        safe_error = dict(run.error or {})
        return trace.model_copy(
            update={
                "state": run.state.value,
                "state_revision": run.state_revision,
                "state_transitions": transitions,
                "thread_id": run.thread_id,
                "current_stage": stage_by_state[run.state],
                "run_status": run_status,
                "recoverable": (
                    run.state is ContentResearchState.RECOVERY_REQUIRED
                    and bool(safe_error.get("retryable"))
                ),
                "llm_recovery": (
                    {
                        "required": True,
                        "error_code": safe_error.get("code"),
                        "recovery_action": safe_error.get("recovery_action"),
                        "message": safe_error.get("message"),
                    }
                    if run.state is ContentResearchState.RECOVERY_REQUIRED
                    and safe_error.get("stage") == "presearch"
                    else {}
                ),
            }
        )

    async def submit_brand_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        return await self._submit_human_decision(
            workflow_run_id=workflow_run_id,
            target_type="brand_candidate",
            request=request,
            user_id=user_id,
        )

    async def submit_content_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        return await self._submit_human_decision(
            workflow_run_id=workflow_run_id,
            target_type="recommended_content",
            request=request,
            user_id=user_id,
        )

    def list_human_decisions(self, workflow_run_id: str) -> HumanDecisionsResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        return self._decision_service.list_decisions(workflow_run_id)

    async def _submit_human_decision(
        self,
        *,
        workflow_run_id: str,
        target_type: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        response = await self._decision_service.submit_decision(
            brief=brief,
            target_type=target_type,
            request=request,
            user_id=user_id,
        )
        if response.idempotent_replay:
            advancement = self._decision_advancement_service.describe(
                brief=brief, decision=response
            )
            return response.model_copy(
                update={"advancement": {**response.advancement, **advancement}}
            )
        advancement = self._decision_advancement_service.advance(brief=brief, decision=response)
        await self._workflow_runtime.append_event(
            workflow_run_id=workflow_run_id,
            thread_id=brief.thread_id,
            event_type="decision_deep_research_advanced",
            payload={
                "schema_version": "content_research_workflow_event_payload_v1",
                "decision_id": response.decision_id,
                **advancement,
            },
        )
        return response.model_copy(update={"advancement": {**response.advancement, **advancement}})

    async def execute_decision_deep_research_task(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        limit: int = 20,
    ) -> ContentResearchSubagentTaskResponse:
        task = next(
            (
                item
                for item in self._store.list_subagent_tasks_for_workflow(workflow_run_id)
                if item.id == task_id
            ),
            None,
        )
        if task is None:
            raise ContentResearchNotFoundError(f"Deep research task not found: {task_id}")
        if task.payload.get("task_type") != "decision_deep_research":
            raise ContentResearchValidationError("Task is not a decision deep-research task")
        if task.status != "queued":
            return _subagent_task_response(task)
        completed = await self._task_router.execute_task(task, limit=limit)
        return _subagent_task_response(completed)

    def create_result_snapshot(
        self,
        workflow_run_id: str,
        *,
        result_type: str = "topic_research",
        manifest: CoverageManifest | None = None,
        coverage_snapshot: CoverageSnapshot | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> SnapshotResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        plans = self._store.list_plans_for_brief(brief.id)
        plan = plans[-1] if plans else None
        existing = self._store.list_result_snapshots_for_workflow(workflow_run_id)
        snapshot_version = str(len(existing) + 1)
        direction_records = self._store.list_directions_for_plan(plan.id) if plan else []
        governed = self._build_governed_snapshot(
            workflow_run_id=workflow_run_id,
            plan_id=plan.id if plan else None,
            direction_records=direction_records,
            manifest=manifest,
            coverage_snapshot=coverage_snapshot,
            execution_context=execution_context,
        )
        governed_input_fingerprint = _governed_input_fingerprint(governed)
        snapshot = ResearchResultSnapshotRecord(
            id=_new_id("rrs"),
            workflow_run_id=workflow_run_id,
            research_brief_id=brief.id,
            research_plan_id=plan.id if plan else None,
            schema_version="content_research_governed_snapshot_v2",
            snapshot_version=snapshot_version,
            result_type=result_type,
            status=governed["publication_state"],
            title=_snapshot_title(brief, result_type),
            executive_summary=governed["executive_summary"],
            findings=list(governed["claim_cards"]),
            limitations=list(governed["limitations_recovery"]),
            metadata={
                "schema_version": "content_research_governed_snapshot_metadata_v2",
                "governed_snapshot": governed,
                "governed_input_fingerprint": governed_input_fingerprint,
                "llm_scope": {
                    "workspace_id": str(brief.payload.get("workspace_id") or ""),
                    "user_id": str(brief.payload.get("user_id") or ""),
                },
            },
        )
        saved = self._store.save_result_snapshot(snapshot)
        return self._snapshot_response(saved)

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

    async def replay_downstream_from_persisted_packets(
        self, replay_input: PersistedPacketReplayInput
    ) -> dict[str, Any]:
        """Replay admission through publication without any collection capability."""
        workflow_run_id = replay_input.workflow_run_id
        brief = replay_input.brief
        snapshot = replay_input.snapshot
        operation_ids_before = {
            item.id
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow_run_id and item.stage_name == "operation"
        }
        packet_ids_before = {
            item.id
            for item in self._store.list_typed_records(DirectionalEvidencePacketRecord)
            if item.workflow_run_id == workflow_run_id
        }
        packet_ids_by_direction: dict[str, list[str]] = {}
        pipeline = DirectionalExecutionPipeline(self._store)
        for direction in replay_input.directions:
            packet_ids_by_direction[direction.direction_id] = list(
                pipeline.replay_admission_from_persisted_packets(
                    replay_input=direction,
                    snapshot=snapshot,
                )
            )
        operation_ids_after_admission = {
            item.id
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow_run_id and item.stage_name == "operation"
        }
        if operation_ids_after_admission != operation_ids_before:
            raise RuntimeError("packet-only admission replay changed provider operations")

        # All tasks were checked terminal above, so this method's executable-task
        # set is empty. It runs only governance, snapshot/report composition, and
        # immutable materialization for the same successful Creator run.
        await self._execute_formal_research(
            brief=brief,
            provider="xiaohongshu",
            source_kind="persisted_packets",
            limit=0,
        )
        operation_ids_after_publication = {
            item.id
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow_run_id and item.stage_name == "operation"
        }
        if operation_ids_after_publication != operation_ids_before:
            raise RuntimeError("downstream replay changed provider operations")
        packet_ids_after = {
            item.id
            for item in self._store.list_typed_records(DirectionalEvidencePacketRecord)
            if item.workflow_run_id == workflow_run_id
        }
        if packet_ids_after != packet_ids_before:
            raise RuntimeError("downstream replay changed evidence packets")
        report = await self.get_lite_report(workflow_run_id=workflow_run_id)
        return {
            "workflow_run_id": workflow_run_id,
            "packet_ids_by_direction": packet_ids_by_direction,
            "provider_operation_count": len(operation_ids_before),
            "publication_state": report.publication.get("state"),
            "report": report.model_dump(mode="json"),
        }

    async def repair_from_persisted_packets(self, workflow_run_id: str) -> dict[str, Any]:
        """Offer packet-only recovery only for the eligible evidence-only report."""
        ownership_error = legacy_recovery_ownership_unavailable(
            self._store, workflow_run_id
        )
        if ownership_error is not None:
            raise ContentResearchValidationError(ownership_error)
        report = await self.get_lite_report(workflow_run_id=workflow_run_id)
        authority = await self._require_legacy_recovery_authority(
            workflow_run_id=workflow_run_id,
            action="repair_from_persisted_packets",
            published_report=report.model_dump(mode="json"),
        )
        publication = report.publication
        if (
            publication.get("state") != "evidence_only_report"
            or publication.get("publication_reason") != "query_subject_not_supported"
        ):
            raise ContentResearchValidationError(
                "Persisted-packet repair is not available for this report"
            )
        recovery_lock = self._recovery_locks.setdefault(workflow_run_id, asyncio.Lock())
        async with recovery_lock:
            replay_input = authority.replay_input
            if replay_input is None:
                raise ContentResearchValidationError(
                    "persisted_packet_replay_input_unavailable"
                )
            replay = await self.replay_downstream_from_persisted_packets(
                replay_input
            )
        return {
            **replay,
            "status": "completed",
            "packet_count": sum(
                len(direction.packets)
                for direction in replay_input.directions
            ),
            "new_collection_count": 0,
        }

    def _build_governed_snapshot(
        self,
        *,
        workflow_run_id: str,
        plan_id: str | None,
        direction_records: list[ResearchDirectionRecord],
        manifest: CoverageManifest | None = None,
        coverage_snapshot: CoverageSnapshot | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> dict[str, Any]:
        policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if policy is None:
            raise ContentResearchValidationError(
                "Governed snapshot requires a frozen policy snapshot"
            )
        direction_ids = {
            str(item.payload.get("direction_id") or item.payload.get("direction_type") or item.id)
            for item in direction_records
        }
        direction_results = [
            item
            for item in self._store.list_typed_records(DirectionResultDecisionRecord)
            if item.policy_snapshot_id == policy.id and item.research_direction_id in direction_ids
        ]
        candidates_by_id = {
            item.id: item
            for direction_id in direction_ids
            for item in self._store.list_claim_candidates(workflow_run_id, direction_id)
            if manifest is None or manifest.owns(item)
        }
        decisions = [
            item
            for item in self._store.list_typed_records(ClaimAdmissionDecisionRecord)
            if item.claim_candidate_id in candidates_by_id and item.policy_snapshot_id == policy.id
        ]
        admitted = sorted(
            (item for item in decisions if item.decision == "admitted"),
            key=lambda item: item.claim_candidate_id,
        )
        decision_ids = {item.id for item in decisions}
        weak_signals = sorted(
            (
                item
                for item in self._store.list_typed_records(WeakSignalRecord)
                if item.admission_decision_id in decision_ids
            ),
            key=lambda item: item.id,
        )
        governance_read = (
            GovernanceReadModelReader(self._store).read_all(
                workflow_run_id=workflow_run_id,
                research_plan_id=plan_id,
            )
            if plan_id is not None
            else None
        )
        if governance_read is not None and manifest is not None:
            allowed_claim_ids = set(candidates_by_id)
            governance_read.cross_direction_records[:] = [
                item
                for item in governance_read.cross_direction_records
                if (claim_ids := set(item.get("claim_ids") or ()))
                and claim_ids <= allowed_claim_ids
            ]
            governance_read.aggregate_claims[:] = [
                item
                for item in governance_read.aggregate_claims
                if (claim_ids := set(item.get("source_claim_ids") or ()))
                and claim_ids <= allowed_claim_ids
            ]
        claim_cards = [
            _governed_claim_card(
                candidate=candidates_by_id[item.claim_candidate_id],
                decision=item,
                packet=self._store.get_typed_record(
                    DirectionalEvidencePacketRecord,
                    candidates_by_id[item.claim_candidate_id].evidence_packet_id,
                ),
            )
            for item in admitted
        ]
        citation_groups = _citation_groups(claim_cards)
        conclusion_checkpoints = sorted(
            (
                item
                for item in self._store.list_typed_records(StageCheckpointRecord)
                if item.workflow_run_id == workflow_run_id
                and item.stage_name == "marketing_conclusion"
                and item.status in {"completed", "insufficient", "tied"}
                and (manifest is None or manifest.owns(item))
            ),
            key=lambda item: (item.created_at, item.id),
        )
        conclusion_fingerprint = (
            conclusion_checkpoints[-1].input_fingerprint if conclusion_checkpoints else None
        )
        conclusion_candidates = {
            item.id: item
            for item in (
                self._store.list_marketing_conclusion_candidates(workflow_run_id, plan_id)
                if plan_id is not None
                else []
            )
            if manifest is None
            or (
                (supporting_ids := set(item.payload.get("supporting_claim_ids") or ()))
                and supporting_ids <= set(candidates_by_id)
            )
        }
        conclusion_decisions = [
            item
            for item in (
                self._store.list_marketing_conclusion_decisions(workflow_run_id, plan_id)
                if plan_id is not None
                else []
            )
            if item.payload.get("input_fingerprint") == conclusion_fingerprint
        ]
        decision_by_track = {item.track: item for item in conclusion_decisions}
        marketing_conclusions = []
        for track in ("need", "value", "message"):
            decision = decision_by_track.get(track)
            if decision is None:
                continue
            candidate = conclusion_candidates.get(str(decision.candidate_id or ""))
            marketing_conclusions.append(
                {
                    "track": track,
                    "state": decision.state,
                    "candidate_id": decision.candidate_id,
                    "statement": (
                        str(candidate.payload.get("statement") or "")
                        if candidate is not None
                        else None
                    ),
                    "supporting_claim_ids": (
                        list(candidate.payload.get("supporting_claim_ids") or [])
                        if candidate is not None
                        else []
                    ),
                    "supporting_note_count": int(
                        decision.payload.get("supporting_note_count") or 0
                    ),
                    "independent_author_count": int(
                        decision.payload.get("independent_author_count") or 0
                    ),
                    "additional_qualified_count": int(
                        decision.payload.get("additional_qualified_count") or 0
                    ),
                    "body_quote_note_count": int(
                        decision.payload.get("body_quote_note_count") or 0
                    ),
                    "reason_codes": list(decision.payload.get("reason_codes") or []),
                }
            )
        report_section_refs = _report_section_refs(
            claim_cards=claim_cards,
            weak_signals=weak_signals,
            governance_read=governance_read,
        )
        result_by_direction = {item.research_direction_id: item for item in direction_results}
        direction_views = [
            {
                "direction_id": direction_id,
                "state": (
                    result_by_direction.get(direction_id).payload.get("state")
                    if direction_id in result_by_direction
                    else "not_started"
                ),
                "direction_result_id": (
                    result_by_direction[direction_id].id
                    if direction_id in result_by_direction
                    else None
                ),
                "admitted_claim_ids": list(
                    claim_id for claim_id in (
                        result_by_direction.get(direction_id).payload.get("admitted_claim_ids")
                        if direction_id in result_by_direction
                        else []
                    )
                    or [] if claim_id in candidates_by_id
                ),
                "limitations": list(
                    (
                        result_by_direction.get(direction_id).payload.get("limitations")
                        if direction_id in result_by_direction
                        else []
                    )
                    or []
                ),
                "recovery_actions": list(
                    (
                        result_by_direction.get(direction_id).payload.get("recovery_actions")
                        if direction_id in result_by_direction
                        else []
                    )
                    or []
                ),
            }
            for direction_id in sorted(direction_ids)
        ]
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        workflow_execution_state = (
            "completed"
            if tasks
            and all(item.status in {"completed", "partial_completed", "failed"} for item in tasks)
            else "pending"
        )
        marketing_states = {str(item.get("state") or "") for item in marketing_conclusions}
        publication_state = (
            "partial_verified_report"
            if "selected" in marketing_states
            else "directional_report"
            if "directional" in marketing_states
            else "partial_verified_report"
            if claim_cards
            else "evidence_only_report"
        )
        limitations = [
            {
                "direction_id": item["direction_id"],
                "limitations": item["limitations"],
                "recovery_actions": item["recovery_actions"],
            }
            for item in direction_views
            if item["limitations"]
            or item["recovery_actions"]
            or item["state"] != "formal_directional_result"
        ]
        frozen_execution: dict[str, Any] = {}
        if execution_context is not None:
            if coverage_snapshot is None:
                raise ContentResearchValidationError(
                    "Governed report snapshot requires explicit execution Coverage lineage"
                )
            unit = self._store.get_scope_execution_unit(
                execution_context.execution_unit_id
            )
            attempt = self._store.get_scope_execution_attempt(
                execution_context.execution_unit_id, execution_context.attempt_no
            )
            contract = next(
                (
                    item
                    for item in self._store.list_scope_contracts(workflow_run_id)
                    if item.id == execution_context.scope_contract_id
                ),
                None,
            )
            coverage_owned = (
                unit is not None
                and (
                    unit.coverage_snapshot_id == coverage_snapshot.id
                    or coverage_snapshot.manifest is not None
                    and coverage_snapshot.manifest.execution_unit_id == unit.id
                    and coverage_snapshot.manifest.attempt_no == execution_context.attempt_no
                )
            )
            if (
                unit is None
                or unit.workflow_run_id != workflow_run_id
                or unit.scope_contract_id != execution_context.scope_contract_id
                or attempt is None
                or contract is None
                or coverage_snapshot.workflow_run_id != workflow_run_id
                or coverage_snapshot.scope_contract_id != manifest.scope_contract_id
                or not coverage_owned
            ):
                raise ContentResearchValidationError(
                    "Governed report snapshot execution lineage is not owned"
                )
            frozen_execution = {
                "execution_lineage": {
                    "scope_contract_id": contract.id,
                    "execution_unit_id": unit.id,
                    "coverage_snapshot_id": coverage_snapshot.id,
                    "successful_attempt_no": execution_context.attempt_no,
                },
                "scope_contract": _scope_contract_payload(contract),
                "coverage_snapshot": _coverage_snapshot_payload(coverage_snapshot),
                "execution_trace": {
                    **ExecutionTraceReader(self._store).read(unit.id),
                    "attempt_no": execution_context.attempt_no,
                },
            }
        return {
            "schema_version": "content_research_governed_snapshot_v2",
            "workflow_execution_state": workflow_execution_state,
            "publication_state": publication_state,
            "publication_reason": "admitted_claims_available"
            if claim_cards
            else "no_admitted_claims",
            "policy_scope": {
                "policy_snapshot_id": policy.id,
                "effective_policy_hash": policy.effective_policy_hash,
                "run_as_of_at": policy.run_as_of_at.isoformat(),
                # The report is a projection of the immutable run policy.  Do
                # not infer release scope from the records that happened to be
                # materialized by a worker.
                "direction_set_version": policy.effective_policy.get("direction_set_version"),
                "direction_ids": list(policy.effective_policy.get("direction_ids") or []),
                "report_compose_mode": policy.effective_policy.get("report_compose_mode"),
                "contract_versions": sorted(
                    {
                        item.schema_version
                        for item in self._store.list_direction_contracts(policy.id)
                    }
                ),
            },
            "direction_results": direction_views,
            "claim_cards": claim_cards,
            "marketing_conclusions": marketing_conclusions,
            "citation_groups": citation_groups,
            "weak_signals": [
                _weak_signal_display(
                    item,
                    decision=next(
                        (
                            decision
                            for decision in decisions
                            if decision.id == item.admission_decision_id
                        ),
                        None,
                    ),
                    candidate=candidates_by_id.get(
                        next(
                            (
                                decision.claim_candidate_id
                                for decision in decisions
                                if decision.id == item.admission_decision_id
                            ),
                            "",
                        )
                    ),
                )
                for item in weak_signals
            ],
            "cross_direction_records": list(
                governance_read.cross_direction_records if governance_read else []
            ),
            "aggregate_claims": list(governance_read.aggregate_claims if governance_read else []),
            "report_section_refs": report_section_refs,
            "limitations_recovery": limitations,
            "checkpoint_summary": _checkpoint_summary(
                self._store, workflow_run_id, manifest=manifest
            ),
            "faithfulness_audit": {"state": "pending"},
            "executive_summary": _governed_summary(claim_cards, publication_state),
            "research_plan_id": plan_id,
            **frozen_execution,
        }

    def get_governance_read_model(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> ContentResearchGovernanceResponse:
        """Return the sole public read model for cross-direction governance."""
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
        """Expose the persisted direction read model without provider raw data."""
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        if offset < 0 or not 1 <= limit <= 50:
            raise ContentResearchValidationError(
                "offset must be non-negative and limit must be 1..50"
            )
        plan = self._store.list_plans_for_brief(brief.id)
        direction_records = self._store.list_directions_for_plan(plan[-1].id) if plan else []
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
        # Detail collection can invalidate an initially complete selection:
        # a selected search card only becomes usable after its required detail
        # fields are collected.  The final detail checkpoint is therefore the
        # authoritative selection state for counts, coverage and status.
        selection = (
            (detail_checkpoint.payload.get("selection") if detail_checkpoint else None)
            or (selection_checkpoint.payload.get("selection") if selection_checkpoint else {})
            or {}
        )
        decisions = list(selection.get("decisions") or [])
        candidates = list((collect.payload.get("candidates") if collect else []) or [])
        packets = packet_read.packets
        projections = packet_read.projections
        selected = [item for item in decisions if item.get("selected")]
        excluded = [item for item in decisions if not item.get("selected")]
        packet_views = [
            _safe_read_model(
                {
                    "packet_id": item.id,
                    "canonical_source_id": item.canonical_source_id,
                    **item.payload,
                }
            )
            for item in packets
        ]
        projection_by_packet = {item.evidence_packet_id: item for item in projections}
        for packet in packet_views:
            projection = projection_by_packet.get(packet["packet_id"])
            if projection:
                packet["selection"] = _safe_read_model(projection.payload)
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
                _safe_read_model(
                    {key: value for key, value in item.payload.items() if key != "candidates"}
                )
                for item in selection_revisions
            ],
            comment_collection=_safe_read_model(
                {
                    key: value
                    for key, value in (
                        comment_checkpoint.payload if comment_checkpoint else {}
                    ).items()
                    if key != "packet_ids"
                }
            ),
            candidates=[_safe_read_model(item) for item in candidates[offset : offset + limit]],
            selections=[_safe_read_model(item) for item in selected[offset : offset + limit]],
            exclusions=[_safe_read_model(item) for item in excluded[offset : offset + limit]],
            packets=packet_views,
            direction_result=direction_result,
            weak_signals=weak_signals,
            offset=offset,
            limit=limit,
        )

    async def _resolve_coverage(
        self, *, workflow_run_id: str, request: ResolveCoverageRequest
    ) -> dict[str, Any]:
        contract = self._store.get_scope_contract(
            workflow_run_id, version=request.scope_contract_version
        )
        if contract is None:
            raise ContentResearchValidationError(
                "Coverage resolution Scope Contract version was not found"
            )
        snapshot = self._store.get_coverage_snapshot_by_id(request.coverage_snapshot_id)
        if (
            snapshot is None
            or snapshot.workflow_run_id != workflow_run_id
            or snapshot.scope_contract_id != contract.id
            or snapshot.scope_contract_version != contract.version
            or snapshot.state != "awaiting_scope_decision"
        ):
            raise ContentResearchValidationError(
                "Coverage resolution requires the explicit persisted unmet coverage snapshot"
            )
        successor_scope_contract = None
        resulting_contract = contract
        target = None
        details: dict[str, Any] = {}
        report_mode = "limited" if request.resolution == "generate_limited_report" else "withheld"

        if request.resolution != "generate_limited_report":
            target = next(
                (
                    item
                    for item in contract.constraints
                    if item.id == request.constraint_id
                    and item.id in snapshot.unmet_constraint_ids
                    and item.mode == "required"
                ),
                None,
            )
            if target is None:
                raise ContentResearchValidationError(
                    "Coverage resolution constraint must be an unmet required constraint"
                )
            if request.resolution == "expand_required_constraint":
                queries = tuple(
                    " ".join(query.split())
                    for query in request.supplementary_queries
                    if query.strip()
                )
                if not 1 <= len(queries) <= 2 or len(set(queries)) != len(queries):
                    raise ContentResearchValidationError(
                        "Coverage expansion requires one or two distinct user-supplied queries"
                    )
                supplementary_scope = build_scope_contract(
                    workflow_run_id=workflow_run_id,
                    research_plan_id=contract.research_plan_id,
                    version=contract.version,
                    schema_version=contract.schema_version,
                    constraints=contract.constraints,
                    query_groups=tuple(
                        ScopeQueryGroupInput(query, query, (target.value,)) for query in queries
                    ),
                )
                if any(
                    group.execution_role == "exploratory"
                    for group in supplementary_scope.query_groups
                ):
                    raise ContentResearchValidationError(
                        "Coverage expansion queries must include the selected required constraint"
                    )
                details = {"supplementary_queries": list(queries)}

            elif request.supplementary_queries:
                raise ContentResearchValidationError(
                    "Constraint relaxation does not accept supplementary queries"
                )
            else:
                relaxed_constraints = tuple(
                    replace(item, mode="preferred") if item.id == target.id else item
                    for item in contract.constraints
                )
                required_values = tuple(
                    item.value for item in relaxed_constraints if item.mode == "required"
                )
                successor_scope_contract = build_scope_contract(
                    workflow_run_id=workflow_run_id,
                    research_plan_id=contract.research_plan_id,
                    version=contract.version + 1,
                    schema_version=contract.schema_version,
                    constraints=relaxed_constraints,
                    query_groups=tuple(
                        ScopeQueryGroupInput(
                            group.suggested_query,
                            group.final_query,
                            tuple(value for value in required_values if value in group.final_query),
                        )
                        for group in contract.query_groups
                    ),
                )
                resulting_contract = successor_scope_contract
                details = {"previous_mode": "required", "new_mode": "preferred"}
        elif request.constraint_id is not None or request.supplementary_queries:
            raise ContentResearchValidationError(
                "Limited-report resolution does not accept constraint changes or queries"
            )

        event = _coverage_resolution_event(
            contract=resulting_contract,
            snapshot=snapshot,
            resolution=request.resolution,
            source_scope_contract_version=contract.version,
            constraint_id=target.id if target is not None else "",
            report_mode=report_mode,
            details=details,
        )
        execution_revision = (
            1 if successor_scope_contract is not None else snapshot.execution_revision + 1
        )
        authorization = ScopeExecutionAuthorization(
            id="sea_"
            + canonical_fingerprint(
                {
                    "coverage_snapshot_id": snapshot.id,
                    "resolution": request.resolution,
                    "scope_contract_id": resulting_contract.id,
                    "constraint_id": request.constraint_id,
                    "supplementary_queries": details.get("supplementary_queries", []),
                }
            )[:24],
            workflow_run_id=workflow_run_id,
            scope_contract_id=resulting_contract.id,
            scope_contract_version=resulting_contract.version,
            coverage_snapshot_id=snapshot.id,
            resolution=request.resolution,
            execution_revision=execution_revision,
            state=(
                "authorized_limited_report"
                if request.resolution == "generate_limited_report"
                else "authorized_collection"
            ),
        )
        continuation = ScopeExecutionContinuation(
            id="sec_"
            + canonical_fingerprint(
                {
                    "authorization_id": authorization.id,
                    "execution_revision": authorization.execution_revision,
                }
            )[:24],
            authorization_id=authorization.id,
            workflow_run_id=workflow_run_id,
            execution_revision=authorization.execution_revision,
            operation=(
                "limited_report"
                if authorization.resolution == "generate_limited_report"
                else "supplementary_collection"
            ),
            supplementary_queries=tuple(
                str(query) for query in details.get("supplementary_queries", ())
            ),
            state="pending",
        )
        try:
            resulting_contract, event, authorization, continuation, _created = (
                self._store.resolve_coverage_and_authorize_execution_atomically(
                    snapshot=snapshot,
                    authorization=authorization,
                    continuation=continuation,
                    event=event,
                    successor_scope_contract=successor_scope_contract,
                )
            )
        except ValueError as exc:
            raise ContentResearchValidationError(str(exc)) from exc

        await self._continue_coverage_execution(
            workflow_run_id=workflow_run_id,
            continuation=continuation,
        )
        execution_unit = (
            self._store.get_scope_execution_unit(authorization.execution_unit_id)
            if authorization.execution_unit_id
            else None
        )
        return _coverage_resolution_result(
            contract=resulting_contract,
            snapshot=snapshot,
            event=event,
            authorization=authorization,
            execution_unit=execution_unit,
            execution_facts=(
                self._store.execution_trace(execution_unit.id)
                if execution_unit is not None
                else []
            ),
        )

    async def _continue_coverage_execution(
        self,
        *,
        workflow_run_id: str,
        continuation: ScopeExecutionContinuation,
    ) -> None:
        """Wake the durable continuation after its authorization is committed.

        A failed wake leaves the append-only authorization in place.  Replaying
        the same action reuses that authorization and retries this idempotent
        wake instead of recording another decision.
        """
        self._store.requeue_scope_execution_continuation(continuation.authorization_id)
        if self._dispatch_wake_event is not None:
            self._dispatch_wake_event.set()

    async def run_workflow_action(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchWorkflowActionRequest,
    ) -> ContentResearchWorkflowActionResponse:
        action = request.action.strip()
        if action not in P0_WORKFLOW_ACTIONS:
            raise ContentResearchValidationError(
                f"Unsupported Content Research workflow action: {action}"
            )

        if action == "cancel":
            cancelled = await self._lifecycle.apply(
                LifecycleCommand(
                    command_id=request.command_id,
                    run_id=workflow_run_id,
                    expected_state=ContentResearchState(request.expected_state),
                    expected_revision=request.expected_revision,
                    kind="cancel",
                    payload=request.payload,
                )
            )
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status="completed",
                result={"run": self._run_projection_payload(cancelled)},
                local_cache_id=cancelled.brief_id,
            )

        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )

        if action == "retry_presearch":
            declared_state = ContentResearchState(request.expected_state)
            if declared_state is not ContentResearchState.RECOVERY_REQUIRED:
                raise LifecycleCommandConflict(
                    "retry_presearch requires expected_state recovery_required"
                )
            response = await self.retry_presearch(
                workflow_run_id,
                command_id=request.command_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
            )
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status=response.status,
                result=response.model_dump(mode="json"),
                local_cache_id=response.brief_id,
            )

        if action == "revise_subject":
            declared_state = ContentResearchState(request.expected_state)
            if declared_state is not ContentResearchState.BRIEF_CONFIRMATION_REQUIRED:
                raise LifecycleCommandConflict(
                    "revise_subject requires expected_state brief_confirmation_required"
                )
            clarification = ContentResearchSubjectRevisionRequest(**request.payload)
            response = await self.revise_subject(
                workflow_run_id=workflow_run_id,
                command_id=request.command_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                clarification_text=clarification.clarification_text,
            )
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status=response.status,
                result=response.model_dump(mode="json"),
                local_cache_id=response.brief_id,
            )

        if action == "confirm_brief":
            confirmation = ContentResearchBriefConfirmationRequest(**request.payload)
            declared_state = ContentResearchState(request.expected_state)
            if declared_state is not ContentResearchState.BRIEF_CONFIRMATION_REQUIRED:
                raise LifecycleCommandConflict(
                    "confirm_brief requires expected_state brief_confirmation_required"
                )
            projection = await self._lifecycle.apply(
                LifecycleCommand(
                    command_id=request.command_id,
                    run_id=workflow_run_id,
                    expected_state=declared_state,
                    expected_revision=request.expected_revision,
                    kind="confirm_brief",
                    payload=self._build_confirm_brief_command_payload(
                        workflow_run_id=workflow_run_id,
                        brief=brief,
                        confirmation=confirmation,
                        command_id=request.command_id,
                    ),
                )
            )
            scope = await self.get_scope_projection(workflow_run_id)
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status="completed",
                result={
                    "run": self._run_projection_payload(projection),
                    "scope": scope.model_dump(mode="json"),
                },
                local_cache_id=brief.id,
            )

        if action == "replace_scope_draft":
            replacement = ReplaceScopeDraftRequest(**request.payload)
            declared_state = ContentResearchState(request.expected_state)
            if declared_state is not ContentResearchState.SCOPE_CONFIRMATION_REQUIRED:
                raise LifecycleCommandConflict(
                    "replace_scope_draft requires expected_state scope_confirmation_required"
                )
            latest = self._store.get_scope_draft(replacement.scope_draft_id)
            if latest is None or latest.workflow_run_id != workflow_run_id:
                raise LifecycleCommandConflict("Scope Draft does not belong to this Run")
            projection = await self._lifecycle.apply(
                LifecycleCommand(
                    command_id=request.command_id,
                    run_id=workflow_run_id,
                    expected_state=declared_state,
                    expected_revision=request.expected_revision,
                    kind="replace_scope_draft",
                    payload=self._build_scope_draft_replacement_payload(
                        latest=latest,
                        replacement=replacement,
                        command_id=request.command_id,
                    ),
                )
            )
            scope = await self.get_scope_projection(workflow_run_id)
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status="completed",
                result={
                    "run": self._run_projection_payload(projection),
                    "scope": scope.model_dump(mode="json"),
                },
                local_cache_id=brief.id,
            )

        raise AssertionError("validated P0 action did not return")

    def _build_confirm_brief_command_payload(
        self,
        *,
        workflow_run_id: str,
        brief: ResearchBriefRecord,
        confirmation: ContentResearchBriefConfirmationRequest,
        command_id: str,
    ) -> dict[str, Any]:
        if confirmation.brief_id != brief.id:
            raise LifecycleCommandConflict("Brief identity does not match current Run")
        allowed_directions = {"product_marketing", "competitor_discovery", "content_performance"}
        selected_directions = tuple(dict.fromkeys(confirmation.selected_directions))
        if any(item not in allowed_directions for item in selected_directions):
            raise ContentResearchValidationError("Brief contains an unsupported research direction")
        structure_payload = dict(brief.payload.get("subject_structure") or {})
        core_entries = [
            item for item in structure_payload.get("core_entities") or [] if isinstance(item, dict)
        ]
        if len(core_entries) != 1:
            raise ContentResearchValidationError("Brief requires one resolved core research object")
        core_object = str(core_entries[0].get("canonical_name") or "").strip()
        if not core_object:
            raise ContentResearchValidationError("Brief core research object is empty")
        raw_intents = [str(item).strip() for item in structure_payload.get("research_intents") or []]
        product_aspect = concrete_product_marketing_aspect(raw_intents[0] if raw_intents else None)
        context_aspect = " ".join(
            str(item).strip() for item in structure_payload.get("context_modifiers") or []
            if str(item).strip()
        ) or None
        plan_id = _stable_command_id("rp", command_id)
        draft = self._build_scope_v2_draft(
            workflow_run_id=workflow_run_id,
            plan_id=plan_id,
            structure_hash=str(brief.payload.get("subject_structure_hash") or "missing"),
            core_object=core_object,
            product_aspect=product_aspect,
            context_aspect=context_aspect,
            command_id=command_id,
        )
        return {
            "brief_id": brief.id,
            "brief_confirmation": {
                "selected_competitors": list(dict.fromkeys(confirmation.selected_competitors)),
                "custom_competitor_input": confirmation.custom_competitor_input,
                "selected_directions": list(selected_directions),
            },
            "plan": {
                "id": plan_id,
                "schema_version": "content_research_plan_v2",
                "payload": {
                    "schema_version": "content_research_plan_v2",
                    "direction_ids": list(selected_directions),
                },
            },
            "directions": [
                {
                    "id": _stable_command_id("rd", f"{command_id}:{direction_id}"),
                    "schema_version": "content_research_direction_v2",
                    "payload": {
                        "direction_id": direction_id,
                        "name": direction_id,
                        "direction_type": direction_id,
                    },
                }
                for direction_id in selected_directions
            ],
            "scope_draft": draft,
        }

    def _build_scope_draft_replacement_payload(
        self,
        *,
        latest: ResearchScopeDraft,
        replacement: ReplaceScopeDraftRequest,
        command_id: str,
    ) -> dict[str, Any]:
        core_object = " ".join(replacement.core_object.split())
        product_aspect = " ".join(
            str(replacement.product_experience_aspect or "").split()
        ) or None
        context_aspect = " ".join(
            str(replacement.context_audience_aspect or "").split()
        ) or None
        return {
            "replaces_scope_draft_id": latest.id,
            "scope_draft": self._build_scope_v2_draft(
                workflow_run_id=latest.workflow_run_id,
                plan_id=latest.research_plan_id,
                structure_hash=latest.structure_hash,
                core_object=core_object,
                product_aspect=product_aspect,
                context_aspect=context_aspect,
                command_id=command_id,
                replaces_scope_draft_id=latest.id,
                origin="user_edited",
            ),
        }

    def _build_scope_v2_draft(
        self,
        *,
        workflow_run_id: str,
        plan_id: str,
        structure_hash: str,
        core_object: str,
        product_aspect: str | None,
        context_aspect: str | None,
        command_id: str,
        replaces_scope_draft_id: str | None = None,
        origin: str = "system_suggested",
    ) -> dict[str, Any]:
        suggestions = compile_product_marketing_query_portfolio(
            core_object=core_object,
            product_experience_aspect=product_aspect,
            context_audience_aspect=context_aspect,
            preserve_explicit_aspects=origin == "user_edited",
        )
        if not 1 <= len(suggestions) <= 3 or any(not item for item in suggestions):
            raise ContentResearchValidationError("Scope Draft requires one to three non-empty queries")
        groups = tuple(
            ScopeQueryGroupInput(
                suggested_query=query,
                final_query=query,
                targeted_required_terms=(core_object,),
                origin=origin,
            )
            for query in suggestions
        )
        draft = build_scope_draft(
            workflow_run_id=workflow_run_id,
            research_plan_id=plan_id,
            structure_hash=structure_hash,
            schema_version=SCOPE_CONTRACT_SCHEMA_VERSION_V2,
            core_object=core_object,
            product_experience_aspect=product_aspect,
            context_audience_aspect=context_aspect,
            constraints=(ScopeConstraint("core_object", "核心对象", core_object, "required"),),
            query_groups=groups,
        )
        draft = replace(draft, id=_stable_command_id("rsd", command_id))
        durable_draft = _scope_draft_payload(draft)
        durable_draft.pop("created_at", None)
        return {
            **durable_draft,
            "audit_event_id": _stable_command_id("sae", command_id),
            "replaces_scope_draft_id": replaces_scope_draft_id,
        }

    async def _retry_failed_report_publication(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchFormalResearchResponse:
        """Re-materialize a report after a safe, terminal publication failure."""
        runtime_snapshot = await self._workflow_runtime.get_runtime_snapshot(
            workflow_run_id
        )
        runtime_run = runtime_snapshot.get("run") or {}
        runtime_status = str(runtime_run.get("status") or "")
        events = await self._workflow_runtime.list_events(workflow_run_id)
        if runtime_status == "failed":
            failed_publication_id = _latest_report_publication_id(
                events,
                event_type="run_failed",
                error_code="report_publication_failed",
            )
        elif runtime_status == "finalizing_report":
            failed_publication_id = _latest_report_publication_id(
                events,
                event_type="run_report_publication_retry_started",
                error_code=None,
            )
        else:
            failed_publication_id = None
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        publication = (
            self._store.get_typed_record(ReportPublicationRecord, failed_publication_id)
            if failed_publication_id is not None
            else None
        )
        if (
            publication is None
            or publication.workflow_run_id != workflow_run_id
            or not _publication_lineage_is_materializable(self._store, publication)
        ):
            raise ContentResearchValidationError(
                "Report publication retry requires the exact persisted failed publication"
            )
        if runtime_status == "failed":
            await self._workflow_runtime.retry_failed_report_publication(
                workflow_run_id=workflow_run_id,
                publication_id=publication.id,
            )
        try:
            artifact = await ReportPublicationMaterializer(
                self._store, self._store._db_path
            ).materialize(publication.id)
            report_artifact_ref = {
                "type": "content_research_report_publication",
                "id": publication.id,
                "artifact_id": artifact.artifact_id,
                "publication_state": publication.publication_state,
            }
            await self._workflow_runtime.complete_report_publication(
                workflow_run_id=workflow_run_id
            )
            await ReportPublicationMaterializer(
                self._store, self._store._db_path
            ).publish_timeline_message(report_artifact_ref["id"])
        except Exception as exc:
            await self._workflow_runtime.fail_formal_research(
                workflow_run_id=workflow_run_id,
                reason={
                    "code": "report_publication_failed",
                    "message": str(exc) or "Report publication failed.",
                    "publication_id": publication.id,
                },
            )
            raise
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        return ContentResearchFormalResearchResponse(
            workflow_run_id=workflow_run_id,
            status="completed",
            task_count=len(tasks),
            completed_task_count=sum(task.status == "completed" for task in tasks),
            partial_completed_task_count=sum(task.status == "partial_completed" for task in tasks),
            failed_tasks=[],
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )

    async def dispatch_formal_research(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchSourceCollectionRequest,
        retry_completed: bool = False,
    ) -> ContentResearchFormalResearchResponse:
        """Persisted task state is the recovery source; HTTP only dispatches it.

        A provider request may take tens of seconds. Keeping it attached to the
        Creator action fetch starves the user-visible Trace/recovery flow.
        """
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        if (
            brief.status != "ready"
            or not self._store.list_plans_for_brief(brief.id)
            or not self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        ):
            raise ContentResearchStateConflictError(
                "Formal research is not ready to dispatch",
                error_code="CONTENT_RESEARCH_FORMAL_RESEARCH_NOT_READY",
                suggested_action="先确认最终版调研 brief，再开始正式调研",
            )
        self._require_frozen_product_marketing_dispatch_contract(brief)
        if not self._store.list_scope_contracts(workflow_run_id):
            raise ContentResearchValidationError("scope_confirmation_required")
        self._require_scope_execution_authority(workflow_run_id=workflow_run_id)
        dispatch = await self._dispatch.enqueue(
            workflow_run_id=workflow_run_id,
            provider=request.provider,
            source_kind=request.source_kind,
            limit=request.limit,
            retry_completed=retry_completed,
        )
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        if self._dispatch_wake_event is not None:
            self._dispatch_wake_event.set()
        return ContentResearchFormalResearchResponse(
            workflow_run_id=workflow_run_id,
            status=str(dispatch["status"]),
            task_count=len(tasks),
            completed_task_count=sum(task.status == "completed" for task in tasks),
            partial_completed_task_count=sum(task.status == "partial_completed" for task in tasks),
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )

    def _require_live_execution_context(
        self,
        context: ExecutionContext | None,
        operation: str,
    ) -> None:
        if context is None:
            return
        if not self._store.execution_context_is_live(context, operation=operation):
            raise ExecutionLeaseFencedError(
                f"execution attempt lease was fenced before {operation}"
            )

    def _require_scope_execution_authority(
        self,
        *,
        workflow_run_id: str,
        execution_authorization: ScopeExecutionAuthorization | None = None,
    ) -> None:
        """Fence formal execution behind the persisted coverage decision.

        A confirmed Scope with no coverage evaluation is the one initial
        collection path and therefore needs no continuation authorization.
        Once coverage is awaiting a user decision, every execution and
        publication path must carry the authorization written by
        ``resolve_coverage``.  The authorization revision intentionally
        advances the source coverage snapshot by one, because it owns the
        resulting continuation attempt.
        """
        contracts = self._store.list_scope_contracts(workflow_run_id)
        if not contracts:
            raise ContentResearchValidationError("scope_confirmation_required")
        if execution_authorization is not None:
            persisted = self._store.get_scope_execution_authorization(execution_authorization.id)
            contract = self._store.get_scope_contract(
                workflow_run_id, version=execution_authorization.scope_contract_version
            )
            source_coverage = self._store.get_coverage_snapshot_by_id(
                execution_authorization.coverage_snapshot_id
            )
            if (
                persisted is None
                or persisted != execution_authorization
                or persisted.workflow_run_id != workflow_run_id
                or contract is None
                or persisted.scope_contract_id != contract.id
                or source_coverage is None
                or source_coverage.workflow_run_id != workflow_run_id
                or source_coverage.state != "awaiting_scope_decision"
            ):
                raise ContentResearchValidationError("scope_execution_authorization_invalid")
            return

        contract = contracts[-1]
        coverage = self._store.get_coverage_snapshot(workflow_run_id, version=contract.version)
        has_persisted_continuation_authority = any(
            item.scope_contract_id == contract.id
            and item.scope_contract_version == contract.version
            for item in self._store.list_scope_execution_authorizations(workflow_run_id)
        )

        if execution_authorization is None and not self._store.initial_execution_eligibility(
            workflow_run_id, contract.id
        ):
            raise ContentResearchValidationError(
                "scope_execution_authorization_required: unresolved coverage decision "
                "already owns this workflow lineage"
            )

        if coverage is None:
            if execution_authorization is None and has_persisted_continuation_authority:
                raise ContentResearchValidationError("scope_execution_authorization_required")
            return
        if coverage.state != "awaiting_scope_decision":
            return
        if execution_authorization is None:
            raise ContentResearchValidationError("scope_execution_authorization_required")

    async def _require_legacy_recovery_authority(
        self,
        *,
        workflow_run_id: str,
        action: str,
        published_report: dict[str, Any] | None = None,
    ) -> LegacyRecoveryAuthority:
        ownership_error = legacy_recovery_ownership_unavailable(
            self._store, workflow_run_id
        )
        if ownership_error is not None:
            raise ContentResearchValidationError(ownership_error)
        authority = await project_legacy_recovery_authority(
            self._store,
            self._store._db_path,
            workflow_run_id,
            published_report=published_report,
        )
        try:
            authority.require(action)
        except LegacyRecoveryActionUnavailableError as exc:
            raise ContentResearchValidationError(
                str(exc)
            ) from exc
        return authority

    def _require_frozen_product_marketing_dispatch_contract(
        self, brief: ResearchBriefRecord
    ) -> None:
        """Reject a marketing dispatch whose persisted frozen contract is incomplete."""
        snapshot = self._store.get_run_policy_snapshot_for_workflow(brief.workflow_run_id)
        effective_policy = dict(snapshot.effective_policy) if snapshot is not None else {}
        locked_plan = effective_policy.get("locked_query_plan")
        directions = locked_plan.get("directions") if isinstance(locked_plan, dict) else None
        requested_directions_value = effective_policy.get("requested_direction_ids")
        if (
            not isinstance(requested_directions_value, list | tuple)
            or not requested_directions_value
            or any(
                not isinstance(item, str) or not item.strip() for item in requested_directions_value
            )
        ):
            raise ContentResearchValidationError(
                "Formal research dispatch requires valid frozen requested directions"
            )
        requested_directions = {item.strip() for item in requested_directions_value}
        if "product_marketing" not in requested_directions:
            return

        scope_contracts = self._store.list_scope_contracts(brief.workflow_run_id)
        current_scope = scope_contracts[-1] if scope_contracts else None
        if (
            current_scope is not None
            and current_scope.schema_version == SCOPE_CONTRACT_SCHEMA_VERSION_V2
        ):
            required_constraint_ids = tuple(
                item.id for item in current_scope.constraints if item.mode == "required"
            )
            if required_constraint_ids != ("core_object",):
                raise ContentResearchValidationError(
                    "Product marketing Scope v2 requires only the frozen core object"
                )
            if not 1 <= len(current_scope.query_groups) <= 3 or any(
                not item.final_query.strip() for item in current_scope.query_groups
            ):
                raise ContentResearchValidationError(
                    "Product marketing Scope v2 requires one to three frozen queries"
                )
            return

        raise ContentResearchValidationError(
            "Historical Scope v1 is read-only and cannot authorize a new dispatch"
        )

    async def _require_presearch_ready_for_confirmation(self, brief: ResearchBriefRecord) -> None:
        payload_status = str(brief.payload.get("status") or brief.status)
        timeout_status = str(brief.payload.get("timeout_status") or "none")
        if (
            timeout_status == "first_timeout"
            or payload_status not in {"completed", "fallback"}
            or brief.status == "final_timeout"
        ):
            raise ContentResearchStateConflictError(
                "Presearch final outcome is not ready",
                error_code="CONTENT_RESEARCH_PRESEARCH_NOT_READY",
                suggested_action="等待预检索最终完成或完成模型配置后重试",
            )

        snapshot = await self._workflow_runtime.get_runtime_snapshot(brief.workflow_run_id)
        presearch_step = next(
            (
                step
                for step in list(snapshot.get("steps") or [])
                if step.get("step_name") == "presearch"
            ),
            None,
        )
        if presearch_step is not None and presearch_step.get("status") != "succeeded":
            raise ContentResearchStateConflictError(
                "Presearch final outcome is not ready",
                error_code="CONTENT_RESEARCH_PRESEARCH_NOT_READY",
                suggested_action="等待预检索最终完成或完成模型配置后重试",
            )

    def _requeue_recoverable_tasks(
        self,
        workflow_run_id: str,
        *,
        provider: str,
        runtime_child_tasks: list[dict] | None = None,
    ) -> list[str]:
        """Make only explicitly recoverable provider failures eligible for a user retry.

        A completed dispatch can represent an evidence-only report, so its job
        state alone cannot decide whether replay is safe.  Provider-operation
        checkpoints are the durable source for that decision.
        """
        checkpoints = self._store.list_typed_records(StageCheckpointRecord)
        recoverable_codes = {
            "auth_required",
            "timeout",
            "transient_error",
            "rate_limited",
            "unavailable",
        }
        recovery_plans: list[
            tuple[SubagentTaskRecord, list[StageCheckpointRecord], set[str], set[str]]
        ] = []
        for task in self._store.list_subagent_tasks_for_workflow(workflow_run_id):
            operations = [
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.workflow_run_id == workflow_run_id
                and checkpoint.subagent_task_id == task.id
                and checkpoint.stage_name == "operation"
                and checkpoint.status != "superseded"
            ]
            if any(checkpoint.status == "outcome_unknown" for checkpoint in operations):
                continue
            recoverable_operation_fingerprints = {
                checkpoint.input_fingerprint
                for checkpoint in operations
                if str((checkpoint.payload.get("completion") or {}).get("failure_code") or "")
                in recoverable_codes
            }
            if not recoverable_operation_fingerprints:
                continue
            recoverable_operations = {
                str(checkpoint.payload.get("operation") or "")
                for checkpoint in operations
                if checkpoint.input_fingerprint in recoverable_operation_fingerprints
            }
            recovery_plans.append(
                (task, operations, recoverable_operation_fingerprints, recoverable_operations)
            )

        if not recovery_plans:
            raise ContentResearchValidationError(
                "No recoverable Content Research provider failure is available for retry."
            )

        has_auth_failure = any(
            str((checkpoint.payload.get("completion") or {}).get("failure_code") or "")
            in {"auth_required", "auth_expired"}
            for _task, operations, _fingerprints, _operation_names in recovery_plans
            for checkpoint in operations
            if checkpoint.status != "superseded"
        )
        if has_auth_failure:
            adapter = self._source_registry.get(provider)
            authentication_ready = getattr(adapter, "authentication_ready", None)
            if not callable(authentication_ready) or not authentication_ready():
                raise ContentResearchValidationError(
                    "Xiaohongshu authentication must succeed before retrying this run."
                )

        runtime_child_by_id = {
            str(item.get("child_task_id") or ""): item
            for item in runtime_child_tasks or []
            if isinstance(item, dict)
        }
        recovery_child_ids: list[str] = []
        for task, _operations, _fingerprints, _operation_names in recovery_plans:
            child_task_id = str(task.payload.get("workflow_child_task_id") or "")
            child = runtime_child_by_id.get(child_task_id)
            if not child_task_id or child is None:
                raise ContentResearchValidationError(
                    "Recoverable specialist is missing its workflow child counter."
                )
            recovery_count = int(child.get("attempt_count") or 0)
            max_attempts = int(child.get("max_attempts") or 3)
            if recovery_count >= max(max_attempts - 1, 0):
                raise ContentResearchValidationError(
                    "Content Research specialist recovery budget is exhausted."
                )
            recovery_child_ids.append(child_task_id)

        for (
            task,
            operations,
            recoverable_operation_fingerprints,
            recoverable_operations,
        ) in recovery_plans:
            # Search pages feed every following directional boundary.  A
            # failed discover attempt may have left an empty aggregate
            # ``collect`` checkpoint behind; replaying it would skip the
            # provider entirely.  Retire only the derived boundaries for this
            # direction, while completed provider operations remain intact.
            reset_derived_stages: set[str] = set()
            if "discover" in recoverable_operations:
                reset_derived_stages.update(
                    {
                        "collect",
                        "selection",
                        "selection_revision",
                        "detail",
                        "comments",
                        "packet",
                        "facts",
                        "admission",
                    }
                )
            if "detail" in recoverable_operations:
                reset_derived_stages.update({"detail", "comments", "packet", "facts", "admission"})
                failed_candidate_ids = {
                    str((checkpoint.payload.get("request") or {}).get("canonical_source_id") or "")
                    for checkpoint in operations
                    if checkpoint.input_fingerprint in recoverable_operation_fingerprints
                } - {""}
                revisions = sorted(
                    (
                        checkpoint
                        for checkpoint in checkpoints
                        if checkpoint.workflow_run_id == workflow_run_id
                        and checkpoint.subagent_task_id == task.id
                        and checkpoint.stage_name == "selection_revision"
                        and checkpoint.status == "completed"
                    ),
                    key=lambda checkpoint: (checkpoint.created_at, checkpoint.id),
                )
                if revisions and failed_candidate_ids:
                    latest_revision = revisions[-1]
                    revision_payload = dict(latest_revision.payload)
                    revision_payload["candidates"] = [
                        {
                            key: value
                            for key, value in dict(candidate).items()
                            if not (
                                str(candidate.get("canonical_id") or "") in failed_candidate_ids
                                and key in {"detail_attempted", "blocking_unavailable"}
                            )
                        }
                        for candidate in revision_payload.get("candidates") or []
                    ]
                    self._store.save_stage_checkpoint(
                        replace(latest_revision, payload=revision_payload)
                    )
            if "comments" in recoverable_operations:
                reset_derived_stages.update({"comments", "packet", "facts", "admission"})
            # Retire the whole recoverable operation lifecycle, including its
            # earlier ``running`` checkpoint.  Otherwise the next pipeline
            # attempt sees that stale start record as an unknown outcome and
            # refuses to call the provider.  Failed collection pages are
            # retired for the same reason; completed siblings stay reusable.
            for checkpoint in checkpoints:
                if (
                    checkpoint.workflow_run_id != workflow_run_id
                    or checkpoint.subagent_task_id != task.id
                ):
                    continue
                is_recoverable_operation = (
                    checkpoint.stage_name == "operation"
                    and checkpoint.input_fingerprint in recoverable_operation_fingerprints
                )
                is_recoverable_collect_page = (
                    checkpoint.stage_name in {"collect_page", "comments_page"}
                    and str(checkpoint.payload.get("operation_fingerprint") or "")
                    in recoverable_operation_fingerprints
                )
                is_discover_derived_checkpoint = checkpoint.stage_name in reset_derived_stages
                if (
                    is_recoverable_operation
                    or is_recoverable_collect_page
                    or is_discover_derived_checkpoint
                ):
                    self._store.save_stage_checkpoint(replace(checkpoint, status="superseded"))
            payload = dict(task.payload)
            payload.pop("output_payload", None)
            payload["status"] = "queued"
            self._store.save_subagent_task(
                replace(task, status="queued", payload=payload, updated_at=utcnow())
            )
        return recovery_child_ids

    async def start_formal_research(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchFormalResearchResponse:
        """Run the queued specialist tasks without a shared collection gate.

        The former parent-level Xiaohongshu collection duplicated work and
        blocked every specialist when it failed. Provider parameters remain a
        per-specialist execution policy, not a parent evidence result.
        """
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        self._require_scope_execution_authority(workflow_run_id=workflow_run_id)
        async with ThreadStore(self._store._db_path) as thread_store:
            if await thread_store.get_thread(brief.thread_id) is None:
                raise ContentResearchValidationError(
                    "Content research cannot start because its Creator thread no longer exists. "
                    "Create a new checklist from an active Creator conversation."
                )
        if not self._store.list_scope_contracts(workflow_run_id):
            raise ContentResearchValidationError("scope_confirmation_required")
        await self._execute_formal_research(
            brief=brief,
            provider=request.provider,
            source_kind=request.source_kind,
            limit=request.limit,
        )
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        failed_tasks = [
            {
                "task_id": task.id,
                "agent_name": task.payload.get("agent_name"),
                "error": (task.payload.get("output_payload") or {}).get("error_message"),
            }
            for task in tasks
            if task.status in {"failed", "outcome_unknown"}
        ]
        completed = sum(task.status == "completed" for task in tasks)
        partial_completed = sum(task.status == "partial_completed" for task in tasks)
        return ContentResearchFormalResearchResponse(
            workflow_run_id=workflow_run_id,
            status="failed" if failed_tasks else "completed",
            task_count=len(tasks),
            completed_task_count=completed,
            partial_completed_task_count=partial_completed,
            failed_tasks=failed_tasks,
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )

    async def execute_claimed_dispatch(
        self,
        *,
        context: DispatchLeaseContext,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchFormalResearchResponse:
        """Execute a normal dispatch through a store view fenced to its exact claim."""
        if not self._store.dispatch_context_is_live(context):
            raise ExecutionLeaseFencedError("dispatch lease was fenced before formal research")
        bind_runtime = getattr(self._workflow_runtime, "for_dispatch_context", None)
        scoped_runtime = (
            bind_runtime(context) if callable(bind_runtime) else self._workflow_runtime
        )
        scoped_service = ContentResearchService(
            store=self._store.for_dispatch_context(context),
            presearch=self._presearch,
            workflow_runtime=scoped_runtime,
            source_registry=self._source_registry,
            analysis_llm=self._analysis_llm,
            report_semantic_auditor=self._report_semantic_auditor,
            dispatch_wake_event=self._dispatch_wake_event,
        )
        return await scoped_service._start_formal_research_for_dispatch(
            workflow_run_id=context.workflow_run_id,
            request=request,
            dispatch_context=context,
        )

    async def _start_formal_research_for_dispatch(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchSourceCollectionRequest,
        dispatch_context: DispatchLeaseContext,
    ) -> ContentResearchFormalResearchResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        self._require_scope_execution_authority(workflow_run_id=workflow_run_id)
        async with ThreadStore(self._store._db_path) as thread_store:
            if await thread_store.get_thread(brief.thread_id) is None:
                raise ContentResearchValidationError(
                    "Content research cannot start because its Creator thread no longer exists. "
                    "Create a new checklist from an active Creator conversation."
                )
        await self._execute_formal_research(
            brief=brief,
            provider=request.provider,
            source_kind=request.source_kind,
            limit=request.limit,
            dispatch_context=dispatch_context,
        )
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        failed_tasks = [
            {
                "task_id": task.id,
                "agent_name": task.payload.get("agent_name"),
                "error": (task.payload.get("output_payload") or {}).get("error_message"),
            }
            for task in tasks
            if task.status in {"failed", "outcome_unknown"}
        ]
        return ContentResearchFormalResearchResponse(
            workflow_run_id=workflow_run_id,
            status="failed" if failed_tasks else "completed",
            task_count=len(tasks),
            completed_task_count=sum(task.status == "completed" for task in tasks),
            partial_completed_task_count=sum(
                task.status == "partial_completed" for task in tasks
            ),
            failed_tasks=failed_tasks,
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )

    def _persist_scope_coverage(
        self,
        workflow_run_id: str,
        *,
        execution_authorization: ScopeExecutionAuthorization | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> Any | None:
        self._require_live_execution_context(execution_context, "coverage_evaluation")
        contracts = self._store.list_scope_contracts(workflow_run_id)
        if not contracts:
            return None
        scope_contract = (
            next(
                (
                    contract
                    for contract in contracts
                    if execution_authorization is not None
                    and contract.id == execution_authorization.scope_contract_id
                ),
                None,
            )
            or contracts[-1]
        )
        source_snapshot = (
            self._store.get_coverage_snapshot_by_id(execution_authorization.coverage_snapshot_id)
            if execution_authorization is not None
            else None
        )
        if (
            execution_authorization is not None
            and execution_authorization.resolution == "generate_limited_report"
        ):
            return source_snapshot
        existing = self._store.get_coverage_snapshot(
            workflow_run_id,
            version=scope_contract.version,
            execution_revision=(
                execution_authorization.execution_revision
                if execution_authorization is not None
                else 1
            ),
        )
        if existing is not None:
            return existing

        run_policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if run_policy is None:
            return None
        direction_contract = next(
            (
                item
                for item in self._store.list_direction_contracts(run_policy.id)
                if item.direction_id == "product_marketing"
            ),
            None,
        )
        if direction_contract is None:
            return None
        sample_policy = self._store.get_sample_policy(direction_contract.sample_policy_id)
        if sample_policy is None:
            return None

        execution_revision = (
            execution_authorization.execution_revision
            if execution_authorization is not None
            else 1
        )
        execution_unit_id = (
            execution_context.execution_unit_id if execution_context is not None else None
        )
        attempt_no = execution_context.attempt_no if execution_context is not None else 0
        packets = [
            packet
            for packet in self._store.list_typed_records(DirectionalEvidencePacketRecord)
            if packet.workflow_run_id == workflow_run_id
            and packet.research_direction_id == "product_marketing"
            and packet.scope_contract_id == scope_contract.id
            and packet.execution_unit_id == execution_unit_id
            and packet.attempt_no == attempt_no
            and packet.execution_revision == execution_revision
        ]
        candidates = tuple(
            {
                **dict(packet.payload.get("field_projection") or {}),
                "canonical_source_id": packet.canonical_source_id,
                "retrieval_context": dict(packet.payload.get("retrieval_context") or {}),
            }
            for packet in packets
        )
        query_group_outcomes: dict[str, dict[str, Any]] = {
            group.id: {
                "status": "unknown",
                "discovered_count": 0,
                "failure_code": None,
            }
            for group in scope_contract.query_groups
        }
        final_pages: dict[str, StageCheckpointRecord] = {}
        execution_task_id = (
            "crt_"
            + canonical_fingerprint(
                {
                    "authorization_id": execution_authorization.id,
                    "direction_id": "product_marketing",
                }
            )[:24]
            if execution_authorization is not None
            else None
        )
        coverage_task_ids = {
            task.id
            for task in self._store.list_subagent_tasks_for_workflow(workflow_run_id)
            if task.direction_id == "product_marketing"
        }
        if execution_task_id is not None:
            coverage_task_ids.add(execution_task_id)
        owned_checkpoints = [
            checkpoint
            for checkpoint in self._store.list_typed_records(StageCheckpointRecord)
            if checkpoint.workflow_run_id == workflow_run_id
            and checkpoint.status == "completed"
            and checkpoint.scope_contract_id == scope_contract.id
            and checkpoint.execution_unit_id == execution_unit_id
            and checkpoint.attempt_no == attempt_no
            and checkpoint.execution_revision == execution_revision
            and checkpoint.subagent_task_id in coverage_task_ids
        ]
        for checkpoint in owned_checkpoints:
            group_id = str(checkpoint.payload.get("query_group_id") or "")
            if (
                checkpoint.workflow_run_id != workflow_run_id
                or checkpoint.stage_name != "collect_page"
                or checkpoint.status != "completed"
                or (
                    group_id not in query_group_outcomes
                    and checkpoint.subagent_task_id != execution_task_id
                )
            ):
                continue
            query_group_outcomes.setdefault(
                group_id,
                {"status": "unknown", "discovered_count": 0, "failure_code": None},
            )
            current = final_pages.get(group_id)
            if current is None or int(checkpoint.payload.get("page_no") or 0) > int(
                current.payload.get("page_no") or 0
            ):
                final_pages[group_id] = checkpoint
        for group_id, checkpoint in final_pages.items():
            query_group_outcomes[group_id] = {
                "status": str(checkpoint.payload.get("status") or "unknown"),
                "discovered_count": int(checkpoint.payload.get("actual_count") or 0),
                "failure_code": checkpoint.payload.get("failure_reason"),
            }
        manifest = CoverageManifest(
            workflow_run_id=workflow_run_id,
            scope_contract_id=scope_contract.id,
            execution_unit_id=execution_unit_id,
            attempt_no=attempt_no,
            execution_revision=execution_revision,
            packet_ids=tuple(sorted(packet.id for packet in packets)),
            checkpoint_ids=tuple(sorted(checkpoint.id for checkpoint in owned_checkpoints)),
        )
        return persist_scope_coverage_evaluation(
            store=self._store,
            contract=scope_contract,
            candidates=candidates,
            query_group_outcomes=query_group_outcomes,
            minimum_samples=sample_policy.minimum_samples,
            minimum_independent_authors=sample_policy.minimum_independent_authors,
            execution_authorization=execution_authorization,
            source_snapshot=source_snapshot,
            execution_context=execution_context,
            manifest=manifest,
        )

    async def execute_execution_unit(
        self,
        claim: ScopeExecutionAttempt,
        continuation: ScopeExecutionContinuation,
    ) -> str:
        """Execute one continuation only through its exact live attempt lease."""
        unit = self._store.get_scope_execution_unit(claim.execution_unit_id)
        if (
            unit is None
            or continuation.execution_unit_id != unit.id
            or claim.state != "running"
            or not claim.lease_token
        ):
            raise ContentResearchValidationError(
                "execution unit requires a running claimed attempt lease"
            )
        context = ExecutionContext(
            execution_unit_id=unit.id,
            attempt_no=claim.attempt_no,
            lease_token=claim.lease_token,
            scope_contract_id=unit.scope_contract_id,
        )
        self._require_live_execution_context(context, "execute_execution_unit")
        bind_runtime = getattr(self._workflow_runtime, "for_execution_context", None)
        scoped_runtime = (
            bind_runtime(context) if callable(bind_runtime) else self._workflow_runtime
        )
        scoped_service = ContentResearchService(
            store=self._store.for_execution_context(context),
            presearch=self._presearch,
            workflow_runtime=scoped_runtime,
            source_registry=self._source_registry,
            analysis_llm=self._analysis_llm,
            report_semantic_auditor=self._report_semantic_auditor,
            dispatch_wake_event=self._dispatch_wake_event,
        )
        await scoped_service.execute_scope_continuation(
            continuation,
            execution_context=context,
        )
        authorization = self._store.get_scope_execution_authorization(continuation.authorization_id)
        if authorization is None:
            raise ContentResearchValidationError(
                "execution unit authorization disappeared before completion"
            )
        if continuation.operation == "supplementary_collection":
            terminal = self._store.get_coverage_snapshot(
                continuation.workflow_run_id,
                version=authorization.scope_contract_version,
                execution_revision=authorization.execution_revision,
            )
            if terminal is None:
                raise ContentResearchValidationError(
                    "execution unit supplementary collection has no terminal Coverage"
                )
        else:
            publication_facts = [
                fact
                for fact in self._store.execution_trace(context.execution_unit_id)
                if fact.kind == "publication_persisted"
                and isinstance(fact.payload.get("publication_id"), str)
            ]
            publication_id = (
                str(publication_facts[-1].payload["publication_id"])
                if publication_facts
                else ""
            )
            publication = (
                self._store.get_typed_record(ReportPublicationRecord, publication_id)
                if publication_id
                else None
            )
            async with WorkflowStore(self._store._db_path) as workflow_store:
                artifacts = await workflow_store.list_artifacts(continuation.workflow_run_id)
            materialized = any(
                (artifact.payload_json or {}).get("report_publication_id") == publication_id
                for artifact in artifacts
            )
            if (
                publication is None
                or publication.workflow_run_id != continuation.workflow_run_id
                or not materialized
            ):
                raise ContentResearchValidationError(
                    "execution unit limited report has no terminal publication"
                )
        self._require_live_execution_context(context, "execution_terminal_postcondition")
        return "completed"

    async def execute_scope_continuation(
        self,
        continuation: ScopeExecutionContinuation,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> None:
        """Execute only the work owned by one persisted authorization command."""
        self._require_live_execution_context(execution_context, "scope_continuation_start")
        authorization = self._store.get_scope_execution_authorization(continuation.authorization_id)
        if authorization is None:
            raise ContentResearchValidationError(
                "scope execution continuation authorization was not found"
            )
        persisted_continuation = next(
            (
                item
                for item in self._store.list_scope_execution_continuations(
                    authorization.workflow_run_id
                )
                if item.authorization_id == authorization.id
            ),
            None,
        )

        def immutable_command(item: ScopeExecutionContinuation) -> tuple[object, ...]:
            return (
                item.id,
                item.authorization_id,
                item.workflow_run_id,
                item.execution_revision,
                item.operation,
                item.supplementary_queries,
            )

        if persisted_continuation is None or immutable_command(
            persisted_continuation
        ) != immutable_command(continuation):
            raise ContentResearchValidationError(
                "scope execution continuation does not match its persisted command"
            )
        if persisted_continuation.state in {"completed", "failed"}:
            raise ContentResearchValidationError("scope execution continuation is not claimable")
        continuation = persisted_continuation
        if (
            authorization.workflow_run_id != continuation.workflow_run_id
            or authorization.execution_revision != continuation.execution_revision
            or (
                continuation.operation == "limited_report"
                and authorization.state != "authorized_limited_report"
            )
            or (
                continuation.operation == "supplementary_collection"
                and authorization.state != "authorized_collection"
            )
        ):
            raise ContentResearchValidationError(
                "scope execution continuation does not match its authorization"
            )
        self._require_scope_execution_authority(
            workflow_run_id=continuation.workflow_run_id,
            execution_authorization=authorization,
        )
        brief = self._store.get_brief_by_workflow(continuation.workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {continuation.workflow_run_id}"
            )
        runtime_snapshot = await self._workflow_runtime.get_runtime_snapshot(
            continuation.workflow_run_id
        )
        runtime_status = str(
            (runtime_snapshot.get("run") or {}).get("status")
            or runtime_snapshot.get("run_status")
            or ""
        )
        formal_step_status = str(
            next(
                (
                    step.get("status")
                    for step in runtime_snapshot.get("steps") or []
                    if step.get("step_name") == "formal_research"
                ),
                "",
            )
        )
        if runtime_status == "waiting_user" or (
            runtime_status == "running" and formal_step_status == "retrying"
        ):
            restart = getattr(self._workflow_runtime, "restart_formal_research_step", None)
            if callable(restart):
                self._require_live_execution_context(execution_context, "restart_formal_research")
                await restart(
                    workflow_run_id=continuation.workflow_run_id,
                    child_task_ids=[],
                )
        if runtime_status == "succeeded":
            return

        executable_task_ids: set[str] = set()
        if continuation.operation == "supplementary_collection":
            base_task = next(
                (
                    task
                    for task in self._store.list_subagent_tasks_for_workflow(
                        continuation.workflow_run_id
                    )
                    if task.direction_id == "product_marketing"
                    and (task.payload.get("workflow_child_task_id") or "")
                ),
                None,
            )
            if base_task is None:
                raise ContentResearchValidationError(
                    "supplementary collection requires the initial product marketing task"
                )
            # A failed collection keeps its task and operation checkpoints as
            # immutable evidence.  An exact replay must therefore use a fresh
            # authorization-owned attempt namespace; reusing the old task ID
            # would make the router restore its terminal failure and skip the
            # provider call forever.
            prior_attempts = [
                task
                for task in self._store.list_subagent_tasks_for_workflow(
                    continuation.workflow_run_id
                )
                if str(task.metadata.get("scope_execution_authorization_id") or "")
                == authorization.id
            ]
            task_id = (
                "crt_"
                + canonical_fingerprint(
                    {
                        "authorization_id": authorization.id,
                        "direction_id": "product_marketing",
                        "attempt": len(prior_attempts) + 1,
                    }
                )[:24]
            )
            existing_task = self._store.get_subagent_task(task_id)
            if existing_task is None:
                self._require_live_execution_context(execution_context, "create_continuation_task")
                input_payload = dict(base_task.payload.get("input_payload") or {})
                input_payload["scope_execution"] = {
                    "authorization_id": authorization.id,
                    "execution_revision": authorization.execution_revision,
                    "supplementary_queries": list(continuation.supplementary_queries),
                }
                payload = {
                    **base_task.payload,
                    "input_payload": input_payload,
                    "status": "queued",
                }
                payload.pop("workflow_child_task_id", None)
                now = utcnow()
                existing_task = SubagentTaskRecord(
                    id=task_id,
                    workflow_run_id=base_task.workflow_run_id,
                    thread_id=base_task.thread_id,
                    schema_version=base_task.schema_version,
                    status="queued",
                    plan_id=base_task.plan_id,
                    direction_id=base_task.direction_id,
                    created_at=now,
                    updated_at=now,
                    payload=payload,
                    metadata={
                        **base_task.metadata,
                        "scope_execution_authorization_id": authorization.id,
                        "execution_revision": authorization.execution_revision,
                        "scope_execution_attempt": len(prior_attempts) + 1,
                        "execution_unit_id": (
                            execution_context.execution_unit_id
                            if execution_context is not None
                            else authorization.execution_unit_id
                        ),
                        "execution_attempt_no": (
                            execution_context.attempt_no if execution_context is not None else None
                        ),
                    },
                )
                self._store.save_subagent_task(existing_task)
            executable_task_ids.add(existing_task.id)

        await self._execute_formal_research(
            brief=brief,
            provider="xiaohongshu",
            source_kind="search_result",
            limit=50,
            execution_authorization=authorization,
            executable_task_ids=executable_task_ids,
            execution_context=execution_context,
        )

    async def _execute_formal_research(
        self,
        *,
        brief: ResearchBriefRecord,
        provider: str,
        source_kind: str,
        limit: int,
        execution_authorization: ScopeExecutionAuthorization | None = None,
        executable_task_ids: set[str] | None = None,
        execution_context: ExecutionContext | None = None,
        dispatch_context: DispatchLeaseContext | None = None,
    ) -> None:
        self._require_live_execution_context(execution_context, "formal_research_start")
        self._require_scope_execution_authority(
            workflow_run_id=brief.workflow_run_id,
            execution_authorization=execution_authorization,
        )
        tasks = self._store.list_subagent_tasks_for_workflow(brief.workflow_run_id)
        # Failed specialists stay on the same parent Run until the user asks
        # for a retry.  Completed siblings are reused; only the failed work is
        # executed again.
        executable_tasks = [
            task
            for task in tasks
            if task.status in {"queued", "pending", "failed"}
            and (executable_task_ids is None or task.id in executable_task_ids)
        ]
        workflow_traces = self._store.list_traces_for_workflow(brief.workflow_run_id)
        trace_id = workflow_traces[0].id if workflow_traces else None
        terminals = await asyncio.gather(
            *(
                self._task_router.execute_task(
                    task,
                    trace_id=trace_id,
                    provider=provider,
                    source_kind=source_kind,
                    limit=limit,
                    # Each specialist owns its own query and source result.
                    source_result=None,
                    execution_context=execution_context,
                    dispatch_context=dispatch_context,
                )
                for task in executable_tasks
            )
        )
        terminal_by_id = {task.id: task for task in terminals}
        # Continuation tasks deliberately have no workflow child: they are
        # authorized follow-up collection, not a replay of the completed
        # initial child.  Their failures must nevertheless fail the durable
        # continuation so its exact action replay can reclaim it.
        if executable_task_ids is not None:
            failed_continuations = [
                task
                for task_id in executable_task_ids
                if (task := terminal_by_id.get(task_id) or self._store.get_subagent_task(task_id))
                is not None
                and task.status in {"failed", "outcome_unknown"}
            ]
            if failed_continuations:
                task = failed_continuations[0]
                output = dict(task.payload.get("output_payload") or {})
                raise ContentResearchValidationError(
                    "Authorized scope continuation task failed: "
                    + str(
                        (output.get("metadata") or {}).get("blocking_failure_code")
                        or output.get("failure_reason")
                        or output.get("error_message")
                        or task.status
                    )
                )
        runtime_state = await self._workflow_runtime.get_runtime_snapshot(brief.workflow_run_id)
        if runtime_state.get("run_status") == "pausing":
            await self._workflow_runtime.acknowledge_pause_at_safe_boundary(
                workflow_run_id=brief.workflow_run_id,
            )
            return
        recoverable_codes = {
            "auth_required",
            "timeout",
            "transient_error",
            "rate_limited",
            "unavailable",
            "llm_auth_invalid",
            "llm_account_unavailable",
            "llm_model_unavailable",
            "llm_rate_limited",
            "llm_service_unavailable",
            "llm_protocol_incompatible",
        }
        recoverable_failure_by_task = {
            checkpoint.subagent_task_id: str(
                (checkpoint.payload.get("completion") or {}).get("failure_code")
            )
            for checkpoint in self._store.list_typed_records(StageCheckpointRecord)
            if checkpoint.workflow_run_id == brief.workflow_run_id
            and checkpoint.stage_name == "operation"
            and checkpoint.status != "superseded"
            and str((checkpoint.payload.get("completion") or {}).get("failure_code") or "")
            in recoverable_codes
        }
        outcomes: list[dict] = []
        for task in tasks:
            terminal = terminal_by_id.get(task.id) or task
            if terminal.status not in {
                "completed",
                "partial_completed",
                "failed",
                "outcome_unknown",
            }:
                raise ContentResearchValidationError(
                    f"Subagent task did not reach a terminal state: {terminal.id} ({terminal.status})"
                )
            output = dict(terminal.payload.get("output_payload") or {})
            child_id = str(terminal.payload.get("workflow_child_task_id") or "")
            if child_id:
                blocking_failure_code = str(
                    (output.get("metadata") or {}).get("blocking_failure_code")
                    or output.get("failure_reason")
                    or recoverable_failure_by_task.get(terminal.id)
                    or ""
                )
                requires_recovery = (
                    terminal.status == "failed" and terminal.id in recoverable_failure_by_task
                )
                outcomes.append(
                    {
                        "child_task_id": child_id,
                        # An interrupted provider operation has no safe replay
                        # path.  Preserve its richer task status while making
                        # the parent workflow enter its standard retry state.
                        "status": "failed"
                        if terminal.status == "outcome_unknown" or requires_recovery
                        else terminal.status,
                        "error": {
                            "code": blocking_failure_code or "formal_research_failed",
                            "message": output.get("error_message")
                            or blocking_failure_code
                            or "Formal research specialist failed.",
                        },
                        "artifact_refs": self._governed_artifact_refs(
                            workflow_run_id=brief.workflow_run_id,
                            direction_id=str(terminal.direction_id or ""),
                            packet_ids=list((output.get("metadata") or {}).get("packet_ids") or []),
                        ),
                    }
                )
        complete = getattr(self._workflow_runtime, "complete_formal_research", None)
        failed_outcomes = [outcome for outcome in outcomes if outcome["status"] == "failed"]
        recoverable_failed_outcomes = [
            outcome
            for outcome in failed_outcomes
            if str((outcome.get("error") or {}).get("code") or "") in recoverable_codes
        ]
        if failed_outcomes:
            if complete is not None:
                await complete(
                    workflow_run_id=brief.workflow_run_id,
                    task_outcomes=outcomes,
                    artifact_refs=[],
                )
            await self._workflow_runtime.append_event(
                workflow_run_id=brief.workflow_run_id,
                thread_id=brief.thread_id,
                event_type="formal_research_needs_retry",
                payload={
                    "schema_version": "content_research_workflow_event_payload_v1",
                    "failed_subagent_task_ids": [
                        outcome["child_task_id"] for outcome in failed_outcomes
                    ],
                    "message": "One or more research specialists failed; retry is required before results can be finalized.",
                },
            )
            if recoverable_failed_outcomes:
                await self._workflow_runtime.wait_for_user_recovery(
                    workflow_run_id=brief.workflow_run_id,
                    reason={
                        "code": "recoverable_specialist_failure",
                        "message": "A provider operation failed and requires a user retry.",
                    },
                )
            else:
                failure = next(
                    (
                        outcome["error"]
                        for outcome in failed_outcomes
                        if isinstance(outcome.get("error"), dict)
                    ),
                    {
                        "code": "formal_research_failed",
                        "message": "Formal research failed.",
                    },
                )
                await self._workflow_runtime.fail_formal_research(
                    workflow_run_id=brief.workflow_run_id,
                    reason=failure,
                )
            return

        scope_coverage = self._persist_scope_coverage(
            brief.workflow_run_id,
            execution_authorization=execution_authorization,
            execution_context=execution_context,
        )
        limited_report_authorized = scope_coverage is not None and any(
            authorization.coverage_snapshot_id == scope_coverage.id
            and authorization.resolution == "generate_limited_report"
            and authorization.state == "authorized_limited_report"
            for authorization in self._store.list_scope_execution_authorizations(
                brief.workflow_run_id
            )
        )
        if (
            scope_coverage is not None
            and scope_coverage.state == "awaiting_scope_decision"
            and not limited_report_authorized
        ):
            await self._workflow_runtime.wait_for_user_recovery(
                workflow_run_id=brief.workflow_run_id,
                reason={
                    "code": "awaiting_scope_decision",
                    "message": "Required Scope Contract coverage is unmet.",
                },
            )
            return

        plans = self._store.list_plans_for_brief(brief.id)
        if not plans:
            raise ContentResearchValidationError(
                f"Formal governance requires a research plan: {brief.workflow_run_id}"
            )
        self._require_live_execution_context(execution_context, "governance")
        governance = self._cross_direction_governance.execute(
            workflow_run_id=brief.workflow_run_id,
            research_plan_id=plans[-1].id,
            subagent_task_id=f"governance:{plans[-1].id}",
            action_hypotheses=_requested_action_hypotheses(
                question=str(brief.payload.get("custom_research_question") or ""),
                claim_ids=tuple(
                    _admitted_claim_ids_for_run(
                        self._store,
                        brief.workflow_run_id,
                        manifest=scope_coverage.manifest if scope_coverage is not None else None,
                    )
                ),
            ),
            manifest=scope_coverage.manifest if scope_coverage is not None else None,
        )
        governance_refs = _unique_artifact_refs(
            [
                {"type": "content_research_reconciliation", "id": item.id}
                for item in (*governance.overlaps, *governance.contradictions)
            ]
            + [
                {"type": "content_research_aggregate", "id": item.id}
                for item in governance.aggregates
            ]
        )

        run_policy = self._store.get_run_policy_snapshot_for_workflow(brief.workflow_run_id)
        publication_manifest = scope_coverage.manifest if scope_coverage is not None else None
        if run_policy is not None and isinstance(
            run_policy.effective_policy.get("marketing_conclusion_policy"), dict
        ):
            try:
                marketing_checkpoint = await self._govern_marketing_conclusions(
                    workflow_run_id=brief.workflow_run_id,
                    research_plan_id=plans[-1].id,
                    manifest=publication_manifest,
                )
                if publication_manifest is not None:
                    publication_manifest = self._extend_manifest_with_generated_checkpoints(
                        publication_manifest, (marketing_checkpoint,)
                    )
            except (LLMProviderFailure, MarketingConclusionAnalysisError):
                await self._workflow_runtime.append_event(
                    workflow_run_id=brief.workflow_run_id,
                    thread_id=brief.thread_id,
                    event_type="marketing_conclusion_analysis_unavailable",
                    payload={
                        "schema_version": "content_research_workflow_event_payload_v1",
                        "reason_codes": ["marketing_analysis_unavailable"],
                        "recovery_action": "repair_model_configuration_and_resume",
                    },
                )
                await self._workflow_runtime.wait_for_user_recovery(
                    workflow_run_id=brief.workflow_run_id,
                    reason={
                        "code": "marketing_analysis_unavailable",
                        "message": "repair_model_configuration_and_resume",
                    },
                )
                return

        if complete is not None:
            self._require_live_execution_context(execution_context, "workflow_completion")
            artifact_refs = _unique_artifact_refs(
                [ref for outcome in outcomes for ref in outcome["artifact_refs"]] + governance_refs
            )
            await complete(
                workflow_run_id=brief.workflow_run_id,
                task_outcomes=outcomes,
                artifact_refs=artifact_refs,
            )
            report_artifact_ref = None
            try:
                self._require_live_execution_context(execution_context, "report_publication")
                # The report artifact is produced while finalizing_report.  It
                # is not publicly readable until complete_report_publication
                # commits the workflow's succeeded state.
                report_artifact_ref = await self._publish_report_after_workflow_completion(
                    workflow_run_id=brief.workflow_run_id,
                    thread_id=brief.thread_id,
                    execution_authorization=execution_authorization,
                    execution_context=execution_context,
                    dispatch_context=dispatch_context,
                    manifest=publication_manifest,
                )
                if report_artifact_ref is not None:
                    complete_report = getattr(
                        self._workflow_runtime, "complete_report_publication", None
                    )
                    if complete_report is not None:
                        await complete_report(workflow_run_id=brief.workflow_run_id)
                        await ReportPublicationMaterializer(
                            self._store,
                            self._store._db_path,
                            execution_context=execution_context,
                            dispatch_context=dispatch_context,
                        ).publish_timeline_message(report_artifact_ref["id"])
            except Exception as exc:
                failed_publication_id = (
                    report_artifact_ref["id"]
                    if report_artifact_ref is not None
                    else getattr(exc, "publication_id", None)
                )
                failure_reason = {
                    "code": "report_publication_failed",
                    "message": str(exc) or "Report publication failed.",
                }
                if failed_publication_id is not None:
                    failure_reason["publication_id"] = failed_publication_id
                await self._workflow_runtime.fail_formal_research(
                    workflow_run_id=brief.workflow_run_id,
                    reason=failure_reason,
                )
                raise
            if report_artifact_ref is not None:
                artifact_refs = _unique_artifact_refs([*artifact_refs, report_artifact_ref])
            await self._workflow_runtime.append_event(
                workflow_run_id=brief.workflow_run_id,
                thread_id=brief.thread_id,
                event_type="formal_research_governed_completed",
                payload={
                    "schema_version": "content_research_governed_completion_v1",
                    "workflow_execution_state": "completed",
                    "publication_state": report_artifact_ref["publication_state"]
                    if report_artifact_ref
                    else "not_published",
                    "report_publication_id": report_artifact_ref["id"]
                    if report_artifact_ref
                    else None,
                    "artifact_refs": artifact_refs,
                    "governance_replayed": governance.replayed,
                },
            )

    async def _govern_marketing_conclusions(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        manifest: CoverageManifest | None = None,
    ) -> StageCheckpointRecord:
        """Analyze and evaluate only durable admitted product-marketing claims."""
        policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if policy is None:
            raise ContentResearchValidationError(
                "Marketing conclusion governance requires the frozen run policy"
            )
        candidates_by_id = {
            item.id: item
            for item in self._store.list_claim_candidates(workflow_run_id, "product_marketing")
            if manifest is None or manifest.owns(item)
        }
        admitted_claims = sorted(
            (
                (decision, candidates_by_id[decision.claim_candidate_id])
                for decision in self._store.list_typed_records(ClaimAdmissionDecisionRecord)
                if decision.policy_snapshot_id == policy.id
                and decision.research_direction_id == "product_marketing"
                and decision.decision == "admitted"
                and decision.claim_candidate_id in candidates_by_id
            ),
            key=lambda item: item[1].id,
        )
        fingerprint = canonical_fingerprint(
            {
                "workflow_run_id": workflow_run_id,
                "research_plan_id": research_plan_id,
                "policy": policy.effective_policy.get("marketing_conclusion_policy"),
                "admitted_claims": [
                    {
                        "claim_id": claim.id,
                        "quote_refs": claim.payload.get("quote_refs"),
                    }
                    for _decision, claim in admitted_claims
                ],
                "execution_manifest": (
                    {
                        "scope_contract_id": manifest.scope_contract_id,
                        "execution_unit_id": manifest.execution_unit_id,
                        "attempt_no": manifest.attempt_no,
                        "execution_revision": manifest.execution_revision,
                        "packet_ids": list(manifest.packet_ids),
                    }
                    if manifest is not None
                    else None
                ),
            }
        )
        checkpoint_id = f"scp_{canonical_fingerprint({'run': workflow_run_id, 'stage': 'marketing_conclusion', 'input': fingerprint})[:24]}"
        existing = self._store.get_typed_record(StageCheckpointRecord, checkpoint_id)
        if existing is not None and existing.status in {
            "completed",
            "insufficient",
            "tied",
        }:
            return existing

        started_at = utcnow()
        retry_count = (existing.retry_count + 1) if existing is not None else 0
        try:
            if admitted_claims:
                if self._analysis_llm is None:
                    raise LLMProviderFailure(
                        "llm_configuration_scope_missing",
                        "模型配置作用域不可用",
                        True,
                        None,
                        provider="unresolved",
                        model="unresolved",
                        configuration_source="unresolved",
                    )
                brief = self._store.get_brief_by_workflow(workflow_run_id)
                if brief is None:
                    raise ContentResearchValidationError(
                        "Marketing conclusion analysis requires the run brief"
                    )
                generated = await MarketingConclusionAnalysisService(
                    llm=self._analysis_llm,
                    llm_scope={
                        "llm_scope": {
                            "workspace_id": str(brief.payload.get("workspace_id") or ""),
                            "user_id": str(brief.payload.get("user_id") or ""),
                        }
                    },
                ).generate(
                    workflow_run_id=workflow_run_id,
                    research_plan_id=research_plan_id,
                    policy=policy.effective_policy,
                    admitted_claims=admitted_claims,
                )
            else:
                generated = ()
            packets = {
                claim.evidence_packet_id: self._store.get_typed_record(
                    DirectionalEvidencePacketRecord, claim.evidence_packet_id
                )
                for _decision, claim in admitted_claims
            }
            evaluation = evaluate_marketing_conclusions(
                candidates=generated,
                admitted_claims=admitted_claims,
                packets={key: value for key, value in packets.items() if value is not None},
                policy=policy.effective_policy,
            )
        except (LLMProviderFailure, MarketingConclusionAnalysisError) as exc:
            failure_code = (
                exc.code if isinstance(exc, LLMProviderFailure) else "llm_protocol_incompatible"
            )
            failure_payload = {
                "schema_version": "content_research_marketing_conclusion_checkpoint_v1",
                "reason_codes": ["marketing_analysis_unavailable"],
                "failure_code": failure_code,
                "recovery_action": "repair_model_configuration_and_resume",
            }
            if isinstance(exc, MarketingConclusionAnalysisError):
                failure_payload["failure_detail"] = exc.detail_code
            failure_checkpoint = StageCheckpointRecord(
                checkpoint_id,
                "content_research_stage_checkpoint_v1",
                failure_payload,
                workflow_run_id=workflow_run_id,
                subagent_task_id=f"marketing-conclusion:{research_plan_id}",
                stage_name="marketing_conclusion",
                input_fingerprint=fingerprint,
                status="waiting_user",
                retry_count=retry_count,
                started_at=started_at,
                finished_at=utcnow(),
                scope_contract_id=manifest.scope_contract_id if manifest else None,
                execution_unit_id=manifest.execution_unit_id if manifest else None,
                attempt_no=manifest.attempt_no if manifest else 0,
                execution_revision=manifest.execution_revision if manifest else 1,
            )
            self._store.save_stage_checkpoint(failure_checkpoint)
            for track in ("need", "value", "message"):
                decision_id = f"mcd_{canonical_fingerprint({'input': fingerprint, 'track': track, 'state': 'analysis_unavailable'})[:24]}"
                existing_decision = self._store.get_typed_record(
                    MarketingConclusionDecisionRecord, decision_id
                )
                if existing_decision is None:
                    self._store.save_marketing_conclusion_decision(
                        MarketingConclusionDecisionRecord(
                            decision_id,
                            "marketing_conclusion_decision_v1",
                            {
                                "input_fingerprint": fingerprint,
                                "reason_codes": ["marketing_analysis_unavailable"],
                                "recovery_action": "repair_model_configuration_and_resume",
                            },
                            workflow_run_id=workflow_run_id,
                            research_plan_id=research_plan_id,
                            candidate_id=None,
                            track=track,
                            state="analysis_unavailable",
                        )
                    )
                elif (
                    existing_decision.workflow_run_id != workflow_run_id
                    or existing_decision.research_plan_id != research_plan_id
                    or existing_decision.track != track
                    or existing_decision.state != "analysis_unavailable"
                    or existing_decision.payload.get("input_fingerprint") != fingerprint
                ):
                    raise RuntimeError(
                        "marketing conclusion unavailable decision identity conflict"
                    )
            if isinstance(exc, MarketingConclusionAnalysisError):
                raise LLMProviderFailure(
                    "llm_protocol_incompatible",
                    "模型响应格式不可用",
                    True,
                    None,
                ) from exc
            raise

        for candidate in generated:
            self._store.save_marketing_conclusion_candidate(candidate)
        for track, track_evaluation in evaluation.tracks.items():
            additional_qualified_count = sum(
                outcome.track == track and outcome.candidate_id != track_evaluation.candidate_id
                for outcome in evaluation.catalog
            )
            decision_id = f"mcd_{canonical_fingerprint({'input': fingerprint, 'track': track, 'state': track_evaluation.state})[:24]}"
            self._store.save_marketing_conclusion_decision(
                MarketingConclusionDecisionRecord(
                    decision_id,
                    "marketing_conclusion_decision_v1",
                    {
                        "input_fingerprint": fingerprint,
                        "reason_codes": list(track_evaluation.reason_codes),
                        "supporting_note_count": track_evaluation.supporting_note_count,
                        "independent_author_count": track_evaluation.independent_author_count,
                        "body_quote_note_count": track_evaluation.body_quote_note_count,
                        "additional_qualified_count": additional_qualified_count,
                    },
                    workflow_run_id=workflow_run_id,
                    research_plan_id=research_plan_id,
                    candidate_id=track_evaluation.candidate_id,
                    track=track,
                    state=track_evaluation.state,
                )
            )
        track_states = {item.state for item in evaluation.tracks.values()}
        status = (
            "completed"
            if "selected" in track_states
            else "tied"
            if "no_single_primary_conclusion" in track_states
            else "insufficient"
        )
        checkpoint = StageCheckpointRecord(
            checkpoint_id,
            "content_research_stage_checkpoint_v1",
            {
                "schema_version": "content_research_marketing_conclusion_checkpoint_v1",
                **evaluation.safe_trace_payload(),
            },
            workflow_run_id=workflow_run_id,
            subagent_task_id=f"marketing-conclusion:{research_plan_id}",
            stage_name="marketing_conclusion",
            input_fingerprint=fingerprint,
            status=status,
            retry_count=retry_count,
            started_at=started_at,
            finished_at=utcnow(),
            scope_contract_id=manifest.scope_contract_id if manifest else None,
            execution_unit_id=manifest.execution_unit_id if manifest else None,
            attempt_no=manifest.attempt_no if manifest else 0,
            execution_revision=manifest.execution_revision if manifest else 1,
        )
        self._store.save_stage_checkpoint(checkpoint)
        return checkpoint

    def _extend_manifest_with_generated_checkpoints(
        self,
        manifest: CoverageManifest,
        checkpoints: tuple[StageCheckpointRecord, ...],
    ) -> CoverageManifest:
        """Add only checkpoints durably generated by the manifest-owned governance run."""
        for checkpoint in checkpoints:
            persisted = self._store.get_typed_record(StageCheckpointRecord, checkpoint.id)
            if (
                not manifest.matches(checkpoint)
                or persisted is None
                or not manifest.matches(persisted)
                or persisted.id != checkpoint.id
                or persisted.input_fingerprint != checkpoint.input_fingerprint
                or persisted.status != checkpoint.status
                or checkpoint.status not in {"completed", "insufficient", "tied"}
            ):
                raise ContentResearchValidationError(
                    "governance checkpoint does not belong to the Coverage execution"
                )
        return replace(
            manifest,
            checkpoint_ids=tuple(
                sorted({*manifest.checkpoint_ids, *(item.id for item in checkpoints)})
            ),
        )

    async def _publish_report_after_workflow_completion(
        self,
        *,
        workflow_run_id: str,
        thread_id: str,
        execution_authorization: ScopeExecutionAuthorization | None = None,
        execution_context: ExecutionContext | None = None,
        dispatch_context: DispatchLeaseContext | None = None,
        manifest: CoverageManifest | None = None,
    ) -> dict[str, str] | None:
        """Materialize the report during the dedicated finalization boundary."""
        self._require_scope_execution_authority(
            workflow_run_id=workflow_run_id,
            execution_authorization=execution_authorization,
        )
        async with WorkflowStore(self._store._db_path) as workflow_store:
            run = await workflow_store.get_run(workflow_run_id)
        if run is None or run.status.value != "finalizing_report":
            return None
        async with ThreadStore(self._store._db_path) as thread_store:
            if await thread_store.get_thread(thread_id) is None:
                raise ContentResearchValidationError(
                    f"Creator thread is required to publish formal report: {thread_id}"
                )
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        plans = self._store.list_plans_for_brief(brief.id) if brief is not None else []
        if not plans:
            raise ContentResearchValidationError(
                "Marketing conclusion governance requires a research plan"
            )
        direction_records = self._store.list_directions_for_plan(plans[-1].id)
        coverage = (
            self._store.get_coverage_snapshot_by_id(
                execution_authorization.coverage_snapshot_id
            )
            if execution_authorization is not None
            and execution_authorization.resolution == "generate_limited_report"
            else self._store.get_coverage_snapshot(
                workflow_run_id,
                version=execution_authorization.scope_contract_version,
                execution_revision=execution_authorization.execution_revision,
            )
            if execution_authorization is not None
            else self._store.get_coverage_snapshot(
                workflow_run_id,
                version=self._store.list_scope_contracts(workflow_run_id)[-1].version,
                execution_revision=1,
            )
        )
        coverage_manifest = coverage.manifest if coverage is not None else None
        if (
            execution_authorization is not None
            and (
                coverage is None
                or (
                    execution_authorization.resolution != "generate_limited_report"
                    and coverage.execution_authorization_id != execution_authorization.id
                )
                or (
                    execution_authorization.resolution == "generate_limited_report"
                    and coverage.id != execution_authorization.coverage_snapshot_id
                )
            )
        ):
            raise ContentResearchValidationError(
                "governed snapshot requires the exact authorized Coverage manifest"
            )
        if manifest is None:
            manifest = coverage_manifest
        elif (
            coverage_manifest is None
            or not all(
                getattr(manifest, field) == getattr(coverage_manifest, field)
                for field in (
                    "workflow_run_id",
                    "scope_contract_id",
                    "execution_unit_id",
                    "attempt_no",
                    "execution_revision",
                    "packet_ids",
                )
            )
            or not set(coverage_manifest.checkpoint_ids) <= set(manifest.checkpoint_ids)
        ):
            raise ContentResearchValidationError(
                "governed snapshot manifest must extend the exact persisted Coverage manifest"
            )
        governed = self._build_governed_snapshot(
            workflow_run_id=workflow_run_id,
            plan_id=plans[-1].id,
            direction_records=direction_records,
            manifest=manifest,
            coverage_snapshot=coverage,
            execution_context=execution_context,
        )
        governed_input_fingerprint = _governed_input_fingerprint(governed)
        matching_snapshot_ids = {
            item.id
            for item in self._store.list_result_snapshots_for_workflow(workflow_run_id)
            if item.metadata.get("governed_input_fingerprint") == governed_input_fingerprint
        }
        existing_publication = next(
            (
                item
                for item in reversed(self._store.list_typed_records(ReportPublicationRecord))
                if item.workflow_run_id == workflow_run_id
                and item.research_plan_id == plans[-1].id
                and item.governed_snapshot_id in matching_snapshot_ids
                and _publication_lineage_is_materializable(self._store, item)
            ),
            None,
        )
        if existing_publication is not None:
            try:
                artifact = await ReportPublicationMaterializer(
                    self._store,
                    self._store._db_path,
                    execution_context=execution_context,
                    dispatch_context=dispatch_context,
                ).materialize(existing_publication.id)
            except Exception as exc:
                raise ReportPublicationMaterializationError(
                    existing_publication.id, exc
                ) from exc
            return {
                "type": "content_research_report_publication",
                "id": existing_publication.id,
                "artifact_id": artifact.artifact_id,
                "publication_state": existing_publication.publication_state,
            }
        snapshot_response = self.create_result_snapshot(
            workflow_run_id,
            result_type="governed_research_report",
            manifest=manifest,
            coverage_snapshot=coverage,
            execution_context=execution_context,
        )
        snapshot = next(
            item
            for item in self._store.list_result_snapshots_for_workflow(workflow_run_id)
            if item.id == snapshot_response.snapshot_id
        )
        publication = await self._report_execution.execute(snapshot, self._report_semantic_auditor)
        try:
            artifact = await ReportPublicationMaterializer(
                self._store,
                self._store._db_path,
                execution_context=execution_context,
                dispatch_context=dispatch_context,
            ).materialize(publication.id)
        except Exception as exc:
            raise ReportPublicationMaterializationError(publication.id, exc) from exc
        return {
            "type": "content_research_report_publication",
            "id": publication.id,
            "artifact_id": artifact.artifact_id,
            "publication_state": publication.publication_state,
        }

    def _governed_artifact_refs(
        self,
        *,
        workflow_run_id: str,
        direction_id: str,
        packet_ids: list[str],
    ) -> list[dict]:
        """Return run-scoped formal artifacts; legacy bundles are never completion inputs."""
        snapshot = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        direction_result = next(
            (
                item
                for item in reversed(self._store.list_typed_records(DirectionResultDecisionRecord))
                if item.research_direction_id == direction_id
                and snapshot is not None
                and item.policy_snapshot_id == snapshot.id
            ),
            None,
        )
        candidate_ids = {
            item.id for item in self._store.list_claim_candidates(workflow_run_id, direction_id)
        }
        all_decisions = [
            item
            for item in self._store.list_typed_records(ClaimAdmissionDecisionRecord)
            if item.claim_candidate_id in candidate_ids
        ]
        decisions = [item for item in all_decisions if item.decision == "admitted"]
        decision_ids = {item.id for item in all_decisions}
        weak_signals = [
            item
            for item in self._store.list_typed_records(WeakSignalRecord)
            if item.admission_decision_id in decision_ids
        ]
        refs = [
            {"type": "content_research_directional_packet", "id": packet_id}
            for packet_id in packet_ids
        ]
        if direction_result is not None:
            refs.append({"type": "content_research_direction_result", "id": direction_result.id})
        refs.extend(
            {"type": "content_research_admitted_decision", "id": item.id} for item in decisions
        )
        refs.extend(
            {"type": "content_research_weak_signal", "id": item.id} for item in weak_signals
        )
        return _unique_artifact_refs(refs)

    @staticmethod
    def _action_response(
        *,
        workflow_run_id: str,
        action: str,
        status: str,
        result: dict,
        local_cache_id: str | None,
    ) -> ContentResearchWorkflowActionResponse:
        return ContentResearchWorkflowActionResponse(
            workflow_run_id=workflow_run_id,
            action=action,
            status=status,
            result=result,
            local_cache_id=local_cache_id,
        )

    def _ensure_source_trace(self, brief: ResearchBriefRecord) -> TraceRecord:
        traces = self._store.list_traces_for_workflow(brief.workflow_run_id)
        if traces:
            return traces[0]
        now = utcnow()
        return self._store.save_trace(
            TraceRecord(
                id=_new_id("trc"),
                workflow_run_id=brief.workflow_run_id,
                thread_id=brief.thread_id,
                schema_version="content_research_trace_v1",
                status="running",
                started_at=now,
                payload={
                    "schema_version": "content_research_trace_v1",
                    "trace_type": "formal_research",
                    "stage": "formal_research",
                },
            )
        )

    def _next_observation_sequence(self, trace_id: str) -> int:
        events = self._store.list_observation_events(trace_id)
        return (events[-1].sequence_no + 1) if events else 1

    @staticmethod
    def _source_query_from_brief(brief: ResearchBriefRecord) -> str:
        payload = brief.payload
        return str(
            payload.get("seed_text")
            or payload.get("confirmed_subject")
            or payload.get("subject_confirmation")
            or ""
        ).strip()

    def _save_brief(
        self,
        *,
        attempt_id: str,
        brief_id: str,
        workflow_run_id: str,
        thread_id: str,
        seed_text: str,
        user_note: str | None,
        workspace_id: str,
        user_id: str,
        outcome: PresearchOutcome,
    ) -> ResearchBriefRecord:
        payload = {
            "schema_version": "content_research_brief_v1",
            "attempt_id": attempt_id,
            "seed_text": seed_text,
            "user_note": user_note,
            "workspace_id": workspace_id,
            "user_id": user_id,
            **self._outcome_payload(outcome),
        }
        brief = ResearchBriefRecord(
            id=brief_id,
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            schema_version="content_research_brief_v1",
            status="draft" if outcome.status != "final_timeout" else "final_timeout",
            payload=payload,
        )
        return self._store.save_brief(brief)

    def _save_query_plan_checkpoint(
        self,
        *,
        task: SubagentTaskRecord,
        compiled_plan: CompiledQueryPlan,
    ) -> None:
        fallback_count = 1 if compiled_plan.fallback_group is not None else 0
        logical_role_count = sum(len(item.roles) for item in compiled_plan.primary_groups) + (
            len(compiled_plan.fallback_group.roles)
            if compiled_plan.fallback_group is not None
            else 0
        )
        physical_group_count = len(compiled_plan.primary_groups) + fallback_count
        now = utcnow()
        self._store.save_stage_checkpoint(
            StageCheckpointRecord(
                id=f"scp_{canonical_fingerprint({'run': task.workflow_run_id, 'task': task.id, 'stage': 'query_plan', 'input': compiled_plan.plan_hash})[:24]}",
                schema_version="content_research_stage_checkpoint_v1",
                payload={
                    "schema_version": "content_research_query_plan_checkpoint_v1",
                    "direction_id": task.direction_id,
                    "query_plan_hash": compiled_plan.plan_hash,
                    "query_compiler_version": compiled_plan.compiler_version,
                    "primary_group_count": len(compiled_plan.primary_groups),
                    "fallback_group_count": fallback_count,
                    "merged_group_count": logical_role_count - physical_group_count,
                    "fallback_group_id": (
                        compiled_plan.fallback_group.query_group.id
                        if compiled_plan.fallback_group is not None
                        else None
                    ),
                },
                workflow_run_id=task.workflow_run_id,
                subagent_task_id=task.id,
                stage_name="query_plan",
                input_fingerprint=compiled_plan.plan_hash,
                status="completed",
                started_at=now,
                finished_at=now,
            )
        )

    def _append_event(
        self,
        *,
        trace_id: str,
        workflow_run_id: str,
        thread_id: str,
        sequence_no: int,
        event_type: str,
        event_name: str,
        payload: dict,
    ) -> None:
        self._store.append_observation_event(
            ObservationEventRecord(
                id=_new_id("obs"),
                trace_id=trace_id,
                workflow_run_id=workflow_run_id,
                thread_id=thread_id,
                schema_version="content_research_observation_event_v1",
                status="recorded",
                sequence_no=sequence_no,
                event_type=event_type,
                event_name=event_name,
                timestamp=utcnow(),
                payload=payload,
            )
        )

    @staticmethod
    def _stable_id(prefix: str, identity: str) -> str:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return f"{prefix}_{digest}"

    @staticmethod
    def _outcome_payload(outcome: PresearchOutcome) -> dict:
        checklist = outcome.checklist
        from app.content_research.subject_structure import subject_structure_payload

        return {
            "status": outcome.status,
            "subject_confirmation": checklist.subject_confirmation,
            "competitor_tags": checklist.competitor_tags,
            "research_directions": checklist.research_directions,
            "direction_catalog": list(DIRECTION_CATALOG_V1),
            "custom_competitor_input": checklist.custom_competitor_input,
            "timeout_status": outcome.timeout_status,
            "fallback_used": outcome.fallback_used,
            "error_code": outcome.error_code,
            "error_message": outcome.error_message,
            "recoverable": outcome.recoverable,
            "provider": outcome.provider,
            "model": outcome.model,
            "configuration_source": outcome.configuration_source,
            "subject_structure": (
                subject_structure_payload(checklist.subject_structure)
                if checklist.subject_structure is not None
                else {}
            ),
            "subject_structure_hash": checklist.subject_structure_hash,
            "subject_structure_analysis_state": checklist.subject_structure_analysis_state,
            "subject_structure_analysis_reason_codes": list(
                checklist.subject_structure_analysis_reason_codes
            ),
        }

    @staticmethod
    def _snapshot_response(snapshot: ResearchResultSnapshotRecord) -> SnapshotResponse:
        governed = dict(snapshot.metadata.get("governed_snapshot") or {})
        return SnapshotResponse(
            snapshot_id=snapshot.id,
            workflow_run_id=snapshot.workflow_run_id,
            research_brief_id=snapshot.research_brief_id,
            research_plan_id=snapshot.research_plan_id,
            snapshot_version=snapshot.snapshot_version,
            result_type=snapshot.result_type,
            status=snapshot.status,
            title=snapshot.title,
            executive_summary=snapshot.executive_summary,
            limitations=snapshot.limitations,
            governed_snapshot=governed,
            created_at=snapshot.created_at.isoformat(),
        )

    @staticmethod
    def _response_from_brief(
        brief: ResearchBriefRecord,
        *,
        run_projection: RunProjection | None = None,
    ) -> ContentResearchPresearchResponse:
        payload = brief.payload
        if run_projection is None:
            raise RuntimeError("Presearch response requires an authoritative Run projection")
        return ContentResearchPresearchResponse(
            attempt_id=str(payload["attempt_id"]),
            workflow_run_id=brief.workflow_run_id,
            brief_id=brief.id,
            status=str(payload.get("status") or brief.status),
            subject_confirmation=str(payload.get("subject_confirmation") or ""),
            competitor_tags=list(payload.get("competitor_tags") or []),
            research_directions=list(payload.get("research_directions") or []),
            direction_catalog=list(payload.get("direction_catalog") or DIRECTION_CATALOG_V1),
            custom_competitor_input=str(payload.get("custom_competitor_input") or ""),
            timeout_status=str(payload.get("timeout_status") or "none"),
            fallback_used=bool(payload.get("fallback_used")),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            recoverable=bool(payload.get("recoverable")),
            configuration_source=payload.get("configuration_source"),
            model=payload.get("model"),
            subject_structure=dict(payload.get("subject_structure") or {}),
            subject_structure_hash=payload.get("subject_structure_hash"),
            subject_structure_analysis_state=str(
                payload.get("subject_structure_analysis_state") or "unresolved"
            ),
            subject_structure_analysis_reason_codes=tuple(
                payload.get("subject_structure_analysis_reason_codes") or ()
            ),
            run=ContentResearchService._run_projection_payload(run_projection),
            local_cache_id=brief.id,
        )

    @staticmethod
    def _run_projection_payload(run_projection: RunProjection) -> dict[str, Any]:
        return {
            "run_id": run_projection.run_id,
            "thread_id": run_projection.thread_id,
            "state": run_projection.state.value,
            "state_revision": run_projection.state_revision,
            "entered_at": run_projection.entered_at,
            "allowed_actions": list(run_projection.allowed_actions),
            "reason_code": run_projection.reason_code,
            "error": dict(run_projection.error) if run_projection.error else None,
            "brief_id": run_projection.brief_id,
            "scope_contract_id": run_projection.scope_contract_id,
            "execution_attempt_id": run_projection.execution_attempt_id,
            "coverage_snapshot_id": run_projection.coverage_snapshot_id,
            "publication_id": run_projection.publication_id,
        }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_command_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _normalized_subject_term(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _subagent_task_response(task: SubagentTaskRecord) -> ContentResearchSubagentTaskResponse:
    return ContentResearchSubagentTaskResponse(
        id=task.id,
        plan_id=task.plan_id,
        direction_id=task.direction_id,
        status=task.status,
        payload=task.payload,
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _snapshot_title(brief: ResearchBriefRecord, result_type: str) -> str:
    subject = str(
        brief.payload.get("seed_text")
        or brief.payload.get("confirmed_subject")
        or brief.payload.get("subject_confirmation")
        or "本轮调研"
    ).strip()
    return f"{subject} 内容调研"


def _freeze_adapter_capabilities(
    registry: SourceAdapterRegistry,
) -> dict[str, dict[str, Any]] | None:
    """Read adapter capabilities once while creating a run, then persist them in its snapshot."""
    adapter = registry.get("xiaohongshu")
    capability_method = getattr(adapter, "capabilities", None)
    if not callable(capability_method):
        return None
    capabilities = capability_method()
    return {
        "xiaohongshu": {
            "adapter_version": type(adapter).__name__,
            **{
                item.operation: {
                    "status": item.status,
                    "fields": list(item.fields),
                    **item.limits,
                    "failure_retryability": item.failure_retryability,
                }
                for item in capabilities
            },
        }
    }


def _safe_read_model(value: Any) -> Any:
    """Defence in depth for a public evidence view.

    Packet construction already omits provider raw data; this additionally
    prevents a future metadata field from leaking a token or raw response.
    """
    forbidden = {"raw_payload", "access_token", "token", "cookie", "cookies", "authorization"}
    if isinstance(value, dict):
        return {
            key: _safe_read_model(item)
            for key, item in value.items()
            if key.lower() not in forbidden
        }
    if isinstance(value, list):
        return [_safe_read_model(item) for item in value]
    return value


def _citation_groups(claim_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "citation_id": f"citation_{index}",
            "citation_group_id": f"citation_{index}",
            "display_index": index,
            "claim_candidate_id": card["claim_candidate_id"],
            "admission_decision_id": card["admission_decision_id"],
            "evidence_refs": safe_public_projection(
                [
                    {
                        **ref,
                        "canonical_note_id": card["canonical_source_id"],
                    }
                    for ref in card["evidence_refs"]
                ]
            ),
            "preview_ref": safe_public_projection((card["evidence_refs"] or [None])[0]),
            "report_section_ref": {"section": "formal_observations", "index": index - 1},
        }
        for index, card in enumerate(claim_cards, start=1)
    ]


def _weak_signal_display(
    item: WeakSignalRecord,
    *,
    decision: ClaimAdmissionDecisionRecord | None,
    candidate: ClaimCandidateRecord | None,
) -> dict[str, Any]:
    payload = safe_public_projection(item.payload)
    return {
        "weak_signal_id": item.id,
        "admission_decision_id": item.admission_decision_id,
        "reason_codes": list(payload.get("reason_codes") or []),
        "limitations": list(payload.get("limitations") or []),
        "recovery_actions": list(payload.get("recovery_actions") or []),
        "display_state": str(payload.get("display_state") or "weak_signal"),
        "claim_candidate_id": candidate.id if candidate else None,
        "direction_id": candidate.research_direction_id if candidate else None,
        "evidence_packet_id": candidate.evidence_packet_id if candidate else None,
        "evidence_refs": safe_public_projection(list(candidate.payload.get("quote_refs") or []))
        if candidate
        else [],
        "computed_metrics": safe_public_projection(
            dict(decision.payload.get("computed_metrics") or {})
        )
        if decision
        else {},
    }


def _report_section_refs(
    *,
    claim_cards: list[dict[str, Any]],
    weak_signals: list[WeakSignalRecord],
    governance_read: Any,
) -> dict[str, list[str]]:
    return {
        "formal_observations": [item["claim_candidate_id"] for item in claim_cards],
        "weak_signals": [item.id for item in weak_signals],
        "cross_direction": [
            item["cross_direction_record_id"]
            for item in (governance_read.cross_direction_records if governance_read else [])
        ],
        "aggregate_observations": [
            item["aggregate_claim_id"]
            for item in (governance_read.aggregate_claims if governance_read else [])
        ],
    }


def _safe_trace_summary(store: Any, workflow_run_id: str) -> dict[str, Any]:
    traces = store.list_traces_for_workflow(workflow_run_id)
    event_counts: dict[str, int] = {}
    event_total = 0
    for trace in traces:
        for event in store.list_observation_events(trace.id):
            event_total += 1
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
    return {
        "trace_count": len(traces),
        "trace_ids": [item.id for item in traces],
        "trace_statuses": [item.status for item in traces],
        "observation_event_count": event_total,
        "observation_event_types": dict(sorted(event_counts.items())),
    }


def _checkpoint_summary(
    store: Any,
    workflow_run_id: str,
    *,
    manifest: CoverageManifest | None = None,
) -> dict[str, Any]:
    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id
        and (manifest is None or manifest.owns(item))
    ]
    return {
        "workflow_run_id": workflow_run_id,
        "state": "available",
        "stages": [
            {
                "checkpoint_id": item.id,
                "stage_name": item.stage_name,
                "status": item.status,
                "input_fingerprint": item.input_fingerprint,
                "retry_count": item.retry_count,
                "output_refs": safe_public_projection(list(item.payload.get("output_refs") or [])),
                "failure": safe_public_projection(item.payload.get("failure")),
            }
            for item in sorted(checkpoints, key=lambda item: (item.stage_name, item.id))
        ],
        "trace_summary": _safe_trace_summary(store, workflow_run_id),
    }


def _governed_input_fingerprint(governed: dict[str, Any]) -> str:
    return canonical_fingerprint(
        {
            "execution_lineage": governed.get("execution_lineage"),
            "policy_scope": governed["policy_scope"],
            "research_plan_id": governed["research_plan_id"],
            "direction_results": governed["direction_results"],
            "claim_ids": [item["claim_candidate_id"] for item in governed["claim_cards"]],
            "marketing_conclusions": governed.get("marketing_conclusions") or [],
            "weak_signal_ids": [item["weak_signal_id"] for item in governed["weak_signals"]],
            "cross_direction_ids": [
                item["cross_direction_record_id"] for item in governed["cross_direction_records"]
            ],
            "aggregate_ids": [item["aggregate_claim_id"] for item in governed["aggregate_claims"]],
        }
    )


def _is_season_context(value: str) -> bool:
    return any(marker in value for marker in ("春", "夏", "秋", "冬", "季"))


def _scope_constraint_payload(item: ScopeConstraint) -> dict[str, Any]:
    return {
        "id": item.id,
        "label": item.label,
        "value": item.value,
        "mode": item.mode,
        "allowed_aliases": list(item.allowed_aliases),
    }


def _scope_query_input_payload(item: ScopeQueryGroupInput) -> dict[str, Any]:
    return {
        "suggested_query": item.suggested_query,
        "final_query": item.final_query,
        "targeted_required_terms": list(item.targeted_required_terms),
        "origin": item.origin,
    }


def _scope_query_group_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "suggested_query": item.suggested_query,
        "final_query": item.final_query,
        "origin": item.origin,
        "execution_role": item.execution_role,
    }


def _scope_draft_payload(draft: ResearchScopeDraft) -> dict[str, Any]:
    return {
        "schema_version": draft.schema_version,
        "id": draft.id,
        "workflow_run_id": draft.workflow_run_id,
        "research_plan_id": draft.research_plan_id,
        "structure_hash": draft.structure_hash,
        "core_object": draft.core_object,
        "product_experience_aspect": draft.product_experience_aspect,
        "context_audience_aspect": draft.context_audience_aspect,
        "constraints": [_scope_constraint_payload(item) for item in draft.constraints],
        "query_groups": [_scope_query_input_payload(item) for item in draft.query_groups],
        "created_at": draft.created_at.isoformat(),
    }


def _scope_draft_audit_payload(event: ScopeDraftAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "workflow_run_id": event.workflow_run_id,
        "scope_draft_id": event.scope_draft_id,
        "event_name": event.event_name,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def _scope_contract_payload(contract: Any) -> dict[str, Any]:
    return {
        "id": contract.id,
        "workflow_run_id": contract.workflow_run_id,
        "research_plan_id": contract.research_plan_id,
        "version": contract.version,
        "schema_version": contract.schema_version,
        "constraints": [_scope_constraint_payload(item) for item in contract.constraints],
        "query_groups": [_scope_query_group_payload(item) for item in contract.query_groups],
        "created_at": contract.created_at.isoformat(),
    }


def _scope_audit_payload(event: ScopeAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "workflow_run_id": event.workflow_run_id,
        "scope_contract_id": event.scope_contract_id,
        "scope_contract_version": event.scope_contract_version,
        "event_name": event.event_name,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def _scope_execution_authorization_payload(
    authorization: ScopeExecutionAuthorization,
) -> dict[str, Any]:
    return {
        "id": authorization.id,
        "execution_unit_id": authorization.execution_unit_id,
        "workflow_run_id": authorization.workflow_run_id,
        "scope_contract_id": authorization.scope_contract_id,
        "scope_contract_version": authorization.scope_contract_version,
        "coverage_snapshot_id": authorization.coverage_snapshot_id,
        "resolution": authorization.resolution,
        "execution_revision": authorization.execution_revision,
        "state": authorization.state,
        "created_at": authorization.created_at.isoformat(),
    }


def _scope_execution_unit_projection(
    *,
    execution_unit: ScopeExecutionUnit | None,
    authorization: ScopeExecutionAuthorization | None,
    audit_events: list[dict[str, Any]],
    execution_facts: list[Any],
) -> dict[str, Any] | None:
    """Expose recovery authority without leaking an attempt lease to Creator."""
    if execution_unit is None or authorization is None:
        return None
    latest_attempt_no = max(
        (int(fact.attempt_no) for fact in execution_facts),
        default=0,
    )
    replay_actions: list[dict[str, Any]] = []
    if (
        execution_unit.state == "failed"
        and execution_unit.recovery_state == "replayable"
        and execution_unit.latest_provider_state == "retryable_failed"
    ):
        resolution_event = next(
            (
                event
                for event in reversed(audit_events)
                if event.get("event_name") == "coverage_resolved"
                and str((event.get("payload") or {}).get("coverage_snapshot_id") or "")
                == execution_unit.coverage_snapshot_id
                and str((event.get("payload") or {}).get("resolution") or "")
                == execution_unit.resolution
            ),
            None,
        )
        payload = dict((resolution_event or {}).get("payload") or {})
        replay_request: dict[str, Any] = {
            "scope_contract_version": int(
                payload.get("source_scope_contract_version")
                or authorization.scope_contract_version
            ),
            "coverage_snapshot_id": execution_unit.coverage_snapshot_id,
            "resolution": execution_unit.resolution,
        }
        constraint_id = str(payload.get("constraint_id") or "")
        if constraint_id:
            replay_request["constraint_id"] = constraint_id
        supplementary_queries = [
            str(query)
            for query in payload.get("supplementary_queries") or []
            if str(query).strip()
        ]
        if supplementary_queries:
            replay_request["supplementary_queries"] = supplementary_queries
        replay_actions.append(
            {
                "action": "replay_coverage_decision",
                "available": True,
                "request": replay_request,
            }
        )
    return {
        "id": execution_unit.id,
        "state": execution_unit.state,
        "attempt_no": latest_attempt_no,
        "recovery_state": execution_unit.recovery_state,
        "allowed_actions": replay_actions,
        "trace_summary": {
            "fact_count": len(execution_facts),
            "attempt_count": len({int(fact.attempt_no) for fact in execution_facts}),
            "last_fact_kind": execution_facts[-1].kind if execution_facts else None,
        },
    }


def _coverage_snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "workflow_run_id": snapshot.workflow_run_id,
        "scope_contract_id": snapshot.scope_contract_id,
        "scope_contract_version": snapshot.scope_contract_version,
        "execution_revision": snapshot.execution_revision,
        "source_coverage_snapshot_id": snapshot.source_coverage_snapshot_id,
        "state": snapshot.state,
        "constraint_counts": snapshot.constraint_counts,
        "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
        "created_at": snapshot.created_at.isoformat(),
    }


def _scope_projection_resolutions(
    *,
    contract: Any | None,
    coverage_snapshot: Any | None,
    authorizations: list[ScopeExecutionAuthorization],
) -> list[dict[str, Any]]:
    if contract is None or coverage_snapshot is None:
        return []
    authorized = any(item.coverage_snapshot_id == coverage_snapshot.id for item in authorizations)
    valid_constraint_ids = [
        item.id
        for item in contract.constraints
        if item.id in coverage_snapshot.unmet_constraint_ids and item.mode == "required"
    ]
    decision_open = coverage_snapshot.state == "awaiting_scope_decision" and not authorized
    no_required_constraint_reason = "no_unmet_required_constraints"
    closed_reason = (
        "coverage_resolution_already_authorized"
        if authorized
        else "coverage_resolution_not_required"
    )
    return [
        {
            "action": "expand_required_constraint",
            "available": decision_open and bool(valid_constraint_ids),
            "valid_constraint_ids": valid_constraint_ids if decision_open else [],
            "supplementary_queries_required": True,
            "unavailable_reason": (
                None
                if decision_open and valid_constraint_ids
                else no_required_constraint_reason
                if decision_open
                else closed_reason
            ),
        },
        {
            "action": "generate_limited_report",
            "available": decision_open,
            "valid_constraint_ids": [],
            "supplementary_queries_required": False,
            "unavailable_reason": None if decision_open else closed_reason,
        },
        {
            "action": "relax_constraint",
            "available": decision_open and bool(valid_constraint_ids),
            "valid_constraint_ids": valid_constraint_ids if decision_open else [],
            "supplementary_queries_required": False,
            "unavailable_reason": (
                None
                if decision_open and valid_constraint_ids
                else no_required_constraint_reason
                if decision_open
                else closed_reason
            ),
        },
    ]


def _coverage_resolution_event(
    *,
    contract: Any,
    snapshot: Any,
    resolution: str,
    source_scope_contract_version: int,
    constraint_id: str,
    report_mode: str,
    details: dict[str, Any],
) -> ScopeAuditEvent:
    payload = {
        "schema_version": "content_research_scope_audit_event_v1",
        "coverage_snapshot_id": snapshot.id,
        "resolution": resolution,
        "constraint_id": constraint_id,
        "source_scope_contract_version": source_scope_contract_version,
        "resulting_scope_contract_version": contract.version,
        "report_mode": report_mode,
        "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
        "constraint_counts": snapshot.constraint_counts,
        **details,
    }
    return ScopeAuditEvent(
        id="sae_"
        + canonical_fingerprint(
            {
                "scope_contract_id": contract.id,
                "coverage_snapshot_id": snapshot.id,
                "resolution": resolution,
            }
        )[:24],
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="coverage_resolved",
        payload=payload,
    )


def _coverage_resolution_result(
    *,
    contract: Any,
    snapshot: Any,
    event: ScopeAuditEvent,
    authorization: ScopeExecutionAuthorization,
    execution_unit: ScopeExecutionUnit | None,
    execution_facts: list[Any],
) -> dict[str, Any]:
    return {
        "report_mode": str(event.payload.get("report_mode") or "withheld"),
        "scope_contract": _scope_contract_payload(contract),
        "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
        "audit_event": _scope_audit_payload(event),
        "execution_unit": _scope_execution_unit_projection(
            execution_unit=execution_unit,
            authorization=authorization,
            audit_events=[_scope_audit_payload(event)],
            execution_facts=execution_facts,
        ),
    }


def _publication_lineage_is_materializable(
    store: SQLiteContentResearchStore, publication: ReportPublicationRecord
) -> bool:
    draft = store.get_typed_record(ReportDraftRecord, publication.report_draft_id)
    decision = store.get_typed_record(
        ReportFaithfulnessDecisionRecord, publication.faithfulness_decision_id
    )
    return (
        draft is not None
        and decision is not None
        and decision.report_draft_id == draft.id
        and not any(
            event.event_type == "integrity_flagged"
            for event in store.list_report_integrity_events(publication.id)
        )
    )


def _latest_report_publication_id(
    events: list[dict[str, Any]],
    *,
    event_type: str,
    error_code: str | None,
) -> str | None:
    for event in reversed(events):
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload_json") or {}
        if error_code is not None and payload.get("error_code") != error_code:
            continue
        publication_id = payload.get("publication_id")
        if isinstance(publication_id, str) and publication_id:
            return publication_id
        return None
    return None


def _admitted_claim_ids_for_run(
    store: Any,
    workflow_run_id: str,
    *,
    manifest: CoverageManifest | None = None,
) -> list[str]:
    candidates = {
        item.id
        for item in store.list_typed_records(ClaimCandidateRecord)
        if item.workflow_run_id == workflow_run_id
        and (manifest is None or manifest.owns(item))
    }
    return sorted(
        item.claim_candidate_id
        for item in store.list_typed_records(ClaimAdmissionDecisionRecord)
        if item.decision == "admitted" and item.claim_candidate_id in candidates
    )


def _requested_action_hypotheses(
    *,
    question: str,
    claim_ids: tuple[str, ...],
) -> tuple[ActionHypothesisRequest, ...]:
    normalized = question.strip()
    request_markers = ("下一步", "行动建议", "行动方案", "下一步建议")
    if (
        not normalized
        or not claim_ids
        or not any(marker in normalized for marker in request_markers)
    ):
        return ()
    return (
        ActionHypothesisRequest(
            statement=normalized,
            claim_ids=claim_ids,
            request_origin="user_requested_next_steps",
        ),
    )


def _unique_artifact_refs(refs: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        key = (str(ref.get("type") or ""), str(ref.get("id") or ""), str(ref.get("status") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _governed_claim_card(
    *,
    candidate: Any,
    decision: ClaimAdmissionDecisionRecord,
    packet: DirectionalEvidencePacketRecord | None,
) -> dict[str, Any]:
    payload = dict(candidate.payload)
    evidence_packet_id = candidate.evidence_packet_id
    return {
        "claim_candidate_id": candidate.id,
        "admission_decision_id": decision.id,
        "direction_id": candidate.research_direction_id,
        "claim_type": candidate.claim_type,
        "admission_state": decision.decision,
        "statement": candidate.statement,
        "scope": dict(payload.get("scope") or {}),
        "evidence_packet_id": evidence_packet_id,
        "canonical_source_id": packet.canonical_source_id if packet is not None else None,
        "evidence_refs": list(payload.get("quote_refs") or []),
        "computed_metrics": dict(decision.payload.get("computed_metrics") or {}),
        "limitations": list(
            decision.payload.get("limitations") or decision.payload.get("reason_codes") or []
        ),
    }


def _governed_summary(claim_cards: list[dict], publication_state: str) -> str:
    if not claim_cards:
        return "本轮没有已准入的正式观察；仅保留证据范围、限制与补采建议。"
    prefix = (
        "本轮已有可追溯的正式观察"
        if publication_state == "partial_verified_report"
        else "本轮已有正式观察"
    )
    return f"{prefix}：{claim_cards[0]['statement']}"
