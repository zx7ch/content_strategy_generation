"""Content Research application service."""

from __future__ import annotations

import asyncio
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
    ContentResearchBriefConfirmRequest,
    ContentResearchBriefResponse,
    ContentResearchDirectionEvidenceResponse,
    ContentResearchDirectionResponse,
    ContentResearchFormalResearchResponse,
    ContentResearchGovernanceResponse,
    ContentResearchLiteReportResponse,
    ContentResearchPlanResponse,
    ContentResearchPresearchResponse,
    ContentResearchPublishedReportResponse,
    ContentResearchSourceCollectionRequest,
    ContentResearchSourceCollectionResponse,
    ContentResearchSubagentTaskResponse,
    ContentResearchTraceResponse,
    ContentResearchWorkflowActionRequest,
    ContentResearchWorkflowActionResponse,
    ContentResearchWorkflowEventsResponse,
    ContentResearchWorkflowSummaryResponse,
    EvidenceBundleView,
    HumanDecisionRequest,
    HumanDecisionResponse,
    HumanDecisionsResponse,
    SnapshotResponse,
)
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.contracts import DIRECTION_CATALOG_V1, build_default_snapshot
from app.content_research.decision_policy import DecisionPolicyService
from app.content_research.decisions import ResearchDecisionService
from app.content_research.evidence import EvidenceBundleService, EvidenceService
from app.content_research.evidence.governance_reader import (
    GovernanceReadModelReader,
    safe_public_projection,
)
from app.content_research.evidence.models import EvidenceRecord
from app.content_research.evidence.packet_reader import PacketEvidenceReader
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
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
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
    PublishedReportNotFoundError,
    PublishedReportReader,
)
from app.content_research.runtime import canonical_fingerprint
from app.content_research.sources import (
    SourceAdapterRegistry,
)
from app.content_research.sources.base import (
    CollectCommentsRequest,
    CollectNoteDetailRequest,
    DiscoverCandidatesRequest,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow import (
    BriefConfirmation,
    ResearchDirectionRegistry,
    ResearchPlanBuilder,
    SubagentTaskRouter,
)
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager


class ContentResearchError(ValueError):
    """Base error for Content Research service failures."""


class ContentResearchNotFoundError(ContentResearchError):
    """Raised when a requested Content Research object is missing."""


class ContentResearchValidationError(ContentResearchError):
    """Raised when a request payload is invalid."""


class WorkflowRuntime(Protocol):
    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str: ...

    async def mark_presearch_ready(self, workflow_run_id: str) -> None: ...

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

    async def acknowledge_pause_at_safe_boundary(self, *, workflow_run_id: str) -> dict: ...

    async def complete_formal_research(
        self, *, workflow_run_id: str, task_outcomes: list[dict], artifact_refs: list[dict]
    ) -> bool: ...


class WorkflowRunManagerRuntime:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str:
        async with WorkflowRunManager(self._db_path) as manager:
            run = await manager.start_run(
                thread_id=thread_id,
                user_id=user_id,
                initial_request=seed_text,
            )
            await manager.initialize_steps(
                run.run_id,
                [
                    {"step_name": "presearch", "phase": WorkflowPhase.INTAKE, "max_attempts": 1},
                    {
                        "step_name": "brief_confirm",
                        "phase": WorkflowPhase.INTAKE,
                        "max_attempts": 1,
                    },
                    {"step_name": "plan_build", "phase": WorkflowPhase.INTAKE, "max_attempts": 1},
                    {
                        "step_name": "formal_research",
                        "phase": WorkflowPhase.RETRIEVAL,
                        "max_attempts": 1,
                    },
                ],
            )
            await manager.start_step(run.run_id, "presearch")
            return run.run_id

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        async with WorkflowRunManager(self._db_path) as manager:
            await manager.complete_step(
                workflow_run_id,
                "presearch",
                artifact_refs=[{"type": "content_research_brief_draft"}],
            )
            await manager.advance_to_next_step(workflow_run_id)

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
        if str((snapshot.get("run") or {}).get("status") or "") == "succeeded":
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
        async with WorkflowRunManager(self._db_path) as manager:
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
            await manager.complete_run(workflow_run_id)
        return True

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
        async with WorkflowStore(self._db_path) as store:
            await store.append_event(
                run_id=workflow_run_id,
                thread_id=thread_id,
                event_type=event_type,
                payload=payload,
            )

    async def end_content_research_run(self, *, workflow_run_id: str, thread_id: str) -> dict:
        cancel_status = "not_cancelled"
        async with WorkflowStore(self._db_path) as store:
            run = await store.get_run(workflow_run_id)
        status_value = run.status.value if run is not None else ""
        if status_value in {"running", "pausing", "paused"}:
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
            event_type="content_research_ended",
            payload={
                "schema_version": "content_research_workflow_event_v1",
                "reason": "user_ended_content_research",
                "active_run_cleared": True,
                "cancel_status": cancel_status,
            },
        )
        async with WorkflowStore(self._db_path) as workflow_store:
            await workflow_store.delete_run(workflow_run_id)
        SQLiteContentResearchStore(self._db_path).delete_workflow(workflow_run_id)
        return {
            "schema_version": CONTENT_RESEARCH_API_SCHEMA_VERSION,
            "ended": True,
            "workflow_run_id": workflow_run_id,
            "thread_id": thread_id,
            "active_run_cleared": True,
            # Ending a research run must never delete the Creator conversation:
            # users can revise the checklist and launch a subsequent run in the
            # same chronological chat history.
            "resources_destroyed": False,
            "cancel_status": cancel_status,
        }

    async def pause_content_research_run(self, *, workflow_run_id: str) -> dict:
        async with WorkflowRunManager(self._db_path) as manager:
            run = await manager.pause_run(workflow_run_id, reason="content_research_user_pause")
        return {"workflow_run_id": workflow_run_id, "status": run.status.value, "recoverable": True}

    async def resume_content_research_run(self, *, workflow_run_id: str) -> dict:
        async with WorkflowRunManager(self._db_path) as manager:
            run = await manager.resume_run(workflow_run_id)
        return {"workflow_run_id": workflow_run_id, "status": run.status.value, "recoverable": True}

    async def acknowledge_pause_at_safe_boundary(self, *, workflow_run_id: str) -> dict:
        async with WorkflowRunManager(self._db_path) as manager:
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
        self._presearch = presearch
        self._workflow_runtime = workflow_runtime
        self._direction_registry = ResearchDirectionRegistry()
        self._plan_builder = ResearchPlanBuilder()
        self._trace_service = ContentResearchTraceService(store=store, db_path=store._db_path)
        self._source_registry = source_registry or SourceAdapterRegistry()
        self._bundle_service = EvidenceBundleService(store)
        self._evidence_service = EvidenceService(store)
        self._task_router = SubagentTaskRouter(store=store, source_registry=self._source_registry)
        self._decision_service = ResearchDecisionService(
            store=store, workflow_runtime=workflow_runtime
        )
        self._decision_advancement_service = DecisionAdvancementService(store=store)
        self._decision_policy_service = DecisionPolicyService(store)
        self._cross_direction_governance = CrossDirectionGovernanceService(store)
        self._report_execution = ReportExecutionService(store)
        self._dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
        self._dispatch_wake_event = dispatch_wake_event
        # A configured analysis LLM also supplies the bounded report reviewer.
        # Without one, publication remains safely non-complete.
        self._report_semantic_auditor = report_semantic_auditor or (
            LLMReportSemanticAuditor(analysis_llm)
            if analysis_llm is not None
            else UnavailableReportSemanticAuditor()
        )

    async def submit_presearch(
        self,
        *,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        user_id: str,
    ) -> ContentResearchPresearchResponse:
        normalized_seed = seed_text.strip()
        if not normalized_seed:
            raise ContentResearchValidationError("seed_text is required")

        workflow_run_id = await self._workflow_runtime.start_presearch_run(
            thread_id=thread_id,
            user_id=user_id,
            seed_text=normalized_seed,
        )
        attempt_id = _new_id("att")
        brief_id = _new_id("rb")
        trace_id = _new_id("trc")
        now = utcnow()
        request = PresearchInput(
            seed_text=normalized_seed,
            user_note=user_note,
            thread_id=thread_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

        llm_task = await self._presearch.create_llm_task(request)
        self._save_trace(trace_id, workflow_run_id, thread_id, now)
        self._append_event(
            trace_id=trace_id,
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            sequence_no=1,
            event_type="task_started",
            event_name="presearch_started",
            payload={
                "schema_version": "content_research_observation_event_v1",
                "attempt_id": attempt_id,
                "seed_text": normalized_seed,
            },
        )

        outcome = await self._presearch.wait_for_first_feedback(request=request, task=llm_task)
        brief = self._save_brief(
            attempt_id=attempt_id,
            brief_id=brief_id,
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            seed_text=normalized_seed,
            user_note=user_note,
            outcome=outcome,
        )
        self._append_presearch_outcome_event(
            trace_id=trace_id,
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            sequence_no=2,
            outcome=outcome,
            attempt_id=attempt_id,
        )
        await self._workflow_runtime.mark_presearch_ready(workflow_run_id)

        if (
            llm_task is not None
            and not llm_task.done()
            and outcome.timeout_status == "first_timeout"
        ):
            asyncio.create_task(
                self._finalize_hard_cutoff(
                    request=request,
                    task=llm_task,
                    attempt_id=attempt_id,
                    trace_id=trace_id,
                    sequence_no=3,
                )
            )

        return self._response_from_brief(brief)

    def get_presearch(self, attempt_id: str) -> ContentResearchPresearchResponse:
        brief = self._store.get_brief_by_presearch_attempt(attempt_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Presearch attempt not found: {attempt_id}")
        return self._response_from_brief(brief)

    async def confirm_brief(
        self,
        *,
        brief_id: str,
        confirmation_request: ContentResearchBriefConfirmRequest,
    ) -> ContentResearchWorkflowSummaryResponse:
        brief = self._store.get_brief(brief_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Research brief not found: {brief_id}")
        if brief.status == "final_timeout":
            raise ContentResearchValidationError("Cannot confirm a final-timeout brief")
        selected_direction_ids = self._direction_registry.canonicalize_many(
            confirmation_request.selected_directions
        )
        if not set(selected_direction_ids).issubset(DIRECTION_CATALOG_V1):
            raise ContentResearchValidationError(
                "Selected directions must belong to the Lite direction catalog"
            )
        directions = self._direction_registry.require_many(selected_direction_ids)
        confirmation = BriefConfirmation(
            confirmed_subject=confirmation_request.confirmed_subject.strip(),
            subject_type=confirmation_request.subject_type.strip() or "unknown",
            selected_competitors=_dedupe(confirmation_request.selected_competitors),
            custom_competitors=_dedupe(confirmation_request.custom_competitors),
            selected_directions=selected_direction_ids,
            custom_research_question=confirmation_request.custom_research_question.strip(),
        )
        plan_id = _new_id("rp")
        task_specs = self._task_router.build_task_specs(
            workflow_run_id=brief.workflow_run_id,
            brief_id=brief.id,
            plan_id=plan_id,
            confirmed_subject=confirmation.confirmed_subject,
            selected_competitors=confirmation.selected_competitors,
            custom_competitors=confirmation.custom_competitors,
            custom_research_question=confirmation.custom_research_question,
            directions=directions,
        )
        plan_payload = self._plan_builder.build(
            brief=brief,
            confirmation=confirmation,
            directions=directions,
            task_specs=task_specs,
        )

        updated_brief = replace(
            brief,
            status="ready",
            payload={
                **brief.payload,
                "status": "ready",
                "confirmed_subject": confirmation.confirmed_subject,
                "subject_type": confirmation.subject_type,
                "selected_competitors": confirmation.selected_competitors,
                "custom_competitors": confirmation.custom_competitors,
                "selected_directions": confirmation.selected_directions,
                "custom_research_question": confirmation.custom_research_question,
            },
            updated_at=utcnow(),
        )
        plan = ResearchPlanRecord(
            id=plan_id,
            brief_id=brief.id,
            workflow_run_id=brief.workflow_run_id,
            thread_id=brief.thread_id,
            schema_version="content_research_plan_v1",
            status="draft",
            payload=plan_payload,
        )
        snapshot, sample_policies, direction_contracts = build_default_snapshot(
            snapshot_id=_new_id("rps"),
            workflow_run_id=brief.workflow_run_id,
            brief_id=brief.id,
            plan_id=plan.id,
            direction_ids=selected_direction_ids,
            provider_capabilities=_freeze_adapter_capabilities(self._source_registry),
        )
        saved_directions: list[ResearchDirectionRecord] = []
        saved_tasks: list[SubagentTaskRecord] = []
        for index, direction in enumerate(directions):
            saved_directions.append(
                ResearchDirectionRecord(
                    id=_new_id("rd"),
                    plan_id=plan.id,
                    workflow_run_id=brief.workflow_run_id,
                    thread_id=brief.thread_id,
                    schema_version="content_research_direction_v1",
                    status="proposed",
                    priority=direction.priority,
                    payload={
                        "schema_version": "content_research_direction_v1",
                        "direction_id": direction.id,
                        "name": direction.label,
                        "direction_type": direction.direction_type,
                        "questions": direction.default_questions,
                        "source_scope": direction.source_scope,
                        "expected_evidence_types": direction.expected_evidence_types,
                        "coverage_target": "p0_minimal",
                    },
                )
            )
            saved_tasks.append(
                SubagentTaskRecord(
                    id=_new_id("sat"),
                    workflow_run_id=brief.workflow_run_id,
                    thread_id=brief.thread_id,
                    schema_version="content_research_subagent_task_v1",
                    status="queued",
                    plan_id=plan.id,
                    direction_id=direction.id,
                    payload={**task_specs[index], "sequence_no": index + 1},
                )
            )

        async def persist_confirmation(
            conn: aiosqlite.Connection, workflow_child_task_ids: list[str]
        ) -> None:
            await self._dispatch.persist_confirmation(
                conn,
                brief=updated_brief,
                plan=plan,
                snapshot=snapshot,
                sample_policies=sample_policies,
                direction_contracts=direction_contracts,
                directions=saved_directions,
                tasks=saved_tasks,
                workflow_child_task_ids=workflow_child_task_ids,
            )

        await self._workflow_runtime.complete_brief_and_plan_atomically(
            workflow_run_id=brief.workflow_run_id,
            task_specs=[task.payload for task in saved_tasks],
            confirmation_writer=persist_confirmation,
        )
        if self._dispatch_wake_event is not None:
            self._dispatch_wake_event.set()

        return await self.get_workflow_summary(brief.workflow_run_id)

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
    ) -> ContentResearchWorkflowSummaryResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        plans = self._store.list_plans_for_brief(brief.id)
        plan = plans[-1] if plans else None
        directions = self._store.list_directions_for_plan(plan.id) if plan else []
        tasks = self._store.list_subagent_tasks_for_workflow(workflow_run_id)
        runtime_snapshot = await self._workflow_runtime.get_runtime_snapshot(workflow_run_id)
        return ContentResearchWorkflowSummaryResponse(
            workflow_run_id=workflow_run_id,
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
            local_cache_id=brief.id,
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

    async def get_workflow_trace(self, workflow_run_id: str) -> ContentResearchTraceResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        return await self._trace_service.build_trace(workflow_run_id=workflow_run_id, brief=brief)

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
            },
        )
        saved = self._store.save_result_snapshot(snapshot)
        return self._snapshot_response(saved)

    async def get_published_report(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
        citation_offset: int = 0,
        citation_limit: int = 50,
    ) -> ContentResearchPublishedReportResponse:
        try:
            payload = await PublishedReportReader(self._store, self._store._db_path).read(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                publication_id=publication_id,
                citation_offset=citation_offset,
                citation_limit=citation_limit,
            )
        except PublishedReportNotFoundError as exc:
            raise ContentResearchNotFoundError(str(exc)) from exc
        return ContentResearchPublishedReportResponse(**payload)

    async def get_lite_report(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
    ) -> ContentResearchLiteReportResponse:
        try:
            payload = await LiteReportReader(self._store, self._store._db_path).read(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                publication_id=publication_id,
            )
        except PublishedReportNotFoundError as exc:
            raise ContentResearchNotFoundError(str(exc)) from exc
        return ContentResearchLiteReportResponse(**payload)

    def _build_governed_snapshot(
        self,
        *,
        workflow_run_id: str,
        plan_id: str | None,
        direction_records: list[ResearchDirectionRecord],
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
        }
        decisions = [
            item
            for item in self._store.list_typed_records(ClaimAdmissionDecisionRecord)
            if item.claim_candidate_id in candidates_by_id
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
                    (
                        result_by_direction.get(direction_id).payload.get("admitted_claim_ids")
                        if direction_id in result_by_direction
                        else []
                    )
                    or []
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
        publication_state = "partial_verified_report" if claim_cards else "evidence_only_report"
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
            "checkpoint_summary": _checkpoint_summary(self._store, workflow_run_id),
            "faithfulness_audit": {"state": "pending"},
            "executive_summary": _governed_summary(claim_cards, publication_state),
            "research_plan_id": plan_id,
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
            detail_checkpoint.payload.get("selection") if detail_checkpoint else None
        ) or (
            selection_checkpoint.payload.get("selection") if selection_checkpoint else {}
        ) or {}
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

    def get_evidence_bundle_view(self, bundle_id: str) -> EvidenceBundleView:
        expanded = self._bundle_service.expand_bundle(bundle_id)
        if expanded is None:
            raise ContentResearchNotFoundError(f"Evidence bundle not found: {bundle_id}")
        bundle = expanded.bundle
        bundle = self._decision_policy_service.apply_to_bundle(bundle)
        return EvidenceBundleView(
            bundle_id=bundle.id,
            workflow_run_id=bundle.workflow_run_id,
            research_brief_id=bundle.research_brief_id,
            research_plan_id=bundle.research_plan_id,
            research_direction_id=bundle.research_direction_id,
            status=bundle.status,
            bundle_type=bundle.bundle_type,
            bundle_version=bundle.bundle_version,
            summary=bundle.summary,
            coverage=bundle.coverage,
            retrieval_metrics=bundle.retrieval_metrics,
            faithfulness_metrics=bundle.faithfulness_metrics,
            cross_source_metrics=bundle.cross_source_metrics,
            contradiction_summary=bundle.contradiction_summary,
            citation_coverage=bundle.citation_coverage,
            unsupported_claim_count=bundle.unsupported_claim_count,
            missing_evidence=_normalize_missing_evidence(expanded.missing_evidence),
            priority_policy_id=bundle.priority_policy_id,
            evidence_boundary_policy_id=bundle.evidence_boundary_policy_id,
            decision_card=bundle.decision_card,
            priority=bundle.priority,
            evidence_state=bundle.evidence_state,
            evidence_grade=bundle.evidence_grade,
            claim_scope=bundle.claim_scope,
            next_action=bundle.next_action,
            items=[asdict(item) for item in expanded.items],
            evidence_by_role={
                role: [_evidence_record_view(record) for record in records]
                for role, records in expanded.evidence_by_role.items()
            },
            lineage_by_evidence_id={
                evidence_id: [asdict(lineage) for lineage in lineage_items]
                for evidence_id, lineage_items in expanded.lineage_by_evidence_id.items()
            },
            source_links=expanded.source_links,
            metadata=bundle.metadata,
            created_at=bundle.created_at.isoformat(),
            updated_at=bundle.updated_at.isoformat(),
        )

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

        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )

        if action == "confirm_brief":
            confirmation = ContentResearchBriefConfirmRequest(**request.payload)
            summary = await self.confirm_brief(brief_id=brief.id, confirmation_request=confirmation)
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status="completed",
                result=summary.model_dump(mode="json"),
                local_cache_id=brief.id,
            )

        if action == "end_content_research":
            result = await self._workflow_runtime.end_content_research_run(
                workflow_run_id=workflow_run_id,
                thread_id=brief.thread_id,
            )
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status="completed",
                result=result,
                local_cache_id=brief.id,
            )

        if action == "pause_formal_research":
            result = await self._workflow_runtime.pause_content_research_run(
                workflow_run_id=workflow_run_id
            )
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status=result["status"],
                result=result,
                local_cache_id=brief.id,
            )

        if action == "resume_formal_research":
            result = await self._workflow_runtime.resume_content_research_run(
                workflow_run_id=workflow_run_id
            )
            return self._action_response(
                workflow_run_id=workflow_run_id,
                action=action,
                status=result["status"],
                result=result,
                local_cache_id=brief.id,
            )

        if action not in {"start_formal_research", "retry_formal_research"}:
            raise ContentResearchValidationError(
                f"Unsupported Content Research workflow action: {action}"
            )
        source_request = ContentResearchSourceCollectionRequest(**request.payload)
        retry = action == "retry_formal_research"
        if retry:
            self._requeue_recoverable_tasks(workflow_run_id)
        formal_result = await self.dispatch_formal_research(
            workflow_run_id=workflow_run_id, request=source_request, retry_completed=retry
        )
        return self._action_response(
            workflow_run_id=workflow_run_id,
            action=action,
            status=formal_result.status,
            result=formal_result.model_dump(mode="json"),
            local_cache_id=brief.id,
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

    def _requeue_recoverable_tasks(self, workflow_run_id: str) -> None:
        """Make only explicitly recoverable provider failures eligible for a user retry.

        A completed dispatch can represent an evidence-only report, so its job
        state alone cannot decide whether replay is safe.  Provider-operation
        checkpoints are the durable source for that decision.
        """
        checkpoints = self._store.list_typed_records(StageCheckpointRecord)
        recoverable_codes = {"auth_required", "timeout", "transient_error", "rate_limited", "unavailable"}
        requeued = False
        for task in self._store.list_subagent_tasks_for_workflow(workflow_run_id):
            operations = [
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.workflow_run_id == workflow_run_id
                and checkpoint.subagent_task_id == task.id
                and checkpoint.stage_name == "operation"
            ]
            if any(checkpoint.status == "outcome_unknown" for checkpoint in operations):
                continue
            recoverable_operation_fingerprints = {
                checkpoint.input_fingerprint
                for checkpoint in operations
                if str(
                    (checkpoint.payload.get("completion") or {}).get("failure_code")
                    or ""
                )
                in recoverable_codes
            }
            if not recoverable_operation_fingerprints:
                continue
            recoverable_operations = {
                str(checkpoint.payload.get("operation") or "")
                for checkpoint in operations
                if checkpoint.input_fingerprint in recoverable_operation_fingerprints
            }
            # Search pages feed every following directional boundary.  A
            # failed discover attempt may have left an empty aggregate
            # ``collect`` checkpoint behind; replaying it would skip the
            # provider entirely.  Retire only the derived boundaries for this
            # direction, while completed provider operations remain intact.
            reset_discover_derived_stages = (
                {"collect", "selection", "selection_revision", "detail"}
                if "discover" in recoverable_operations
                else set()
            )
            # Retire the whole recoverable operation lifecycle, including its
            # earlier ``running`` checkpoint.  Otherwise the next pipeline
            # attempt sees that stale start record as an unknown outcome and
            # refuses to call the provider.  Failed collection pages are
            # retired for the same reason; completed siblings stay reusable.
            for checkpoint in checkpoints:
                if checkpoint.workflow_run_id != workflow_run_id or checkpoint.subagent_task_id != task.id:
                    continue
                is_recoverable_operation = (
                    checkpoint.stage_name == "operation"
                    and checkpoint.input_fingerprint in recoverable_operation_fingerprints
                )
                is_recoverable_collect_page = (
                    checkpoint.stage_name == "collect_page"
                    and str(checkpoint.payload.get("failure_reason") or "")
                    in recoverable_codes
                )
                is_discover_derived_checkpoint = (
                    checkpoint.stage_name in reset_discover_derived_stages
                )
                if (
                    is_recoverable_operation
                    or is_recoverable_collect_page
                    or is_discover_derived_checkpoint
                ):
                    self._store.save_stage_checkpoint(
                        replace(checkpoint, status="superseded")
                    )
            payload = dict(task.payload)
            payload.pop("output_payload", None)
            payload["status"] = "queued"
            self._store.save_subagent_task(
                replace(task, status="queued", payload=payload, updated_at=utcnow())
            )
            requeued = True
        if not requeued:
            raise ContentResearchValidationError(
                "No recoverable Content Research provider failure is available for retry."
            )

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

    async def collect_sources(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchSourceCollectionResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        query = (request.query or "").strip() or self._source_query_from_brief(brief)
        if not query:
            raise ContentResearchValidationError("source collection query is required")

        trace = self._ensure_source_trace(brief)
        sequence_no = self._next_observation_sequence(trace.id)
        self._append_event(
            trace_id=trace.id,
            workflow_run_id=workflow_run_id,
            thread_id=brief.thread_id,
            sequence_no=sequence_no,
            event_type="task_started",
            event_name="source_collection_started",
            payload={
                "schema_version": "content_research_observation_event_v1",
                "operation": request.operation,
                "provider": request.provider,
                "query": query,
                "limit": request.limit,
                "sort": request.sort,
            },
        )
        adapter = self._source_registry.get(request.provider)
        context = {"thread_id": brief.thread_id, "brief_id": brief.id}
        if request.operation == "discover_candidates":
            result = await adapter.discover_candidates(
                DiscoverCandidatesRequest(
                    workflow_run_id=workflow_run_id,
                    query=query,
                    limit=request.limit,
                    sort=request.sort,
                    cursor=request.cursor,
                    context=context,
                )
            )
        elif request.operation == "collect_note_detail":
            if not request.note_id:
                raise ContentResearchValidationError(
                    "note_id is required for note detail collection"
                )
            result = await adapter.collect_note_detail(
                CollectNoteDetailRequest(
                    workflow_run_id=workflow_run_id,
                    note_id=request.note_id,
                    note_url=request.note_url or "",
                    required_fields=tuple(request.required_fields),
                    context=context,
                )
            )
        elif request.operation == "collect_comments":
            if not request.note_id:
                raise ContentResearchValidationError("note_id is required for comment collection")
            result = await adapter.collect_comments(
                CollectCommentsRequest(
                    workflow_run_id=workflow_run_id,
                    parent_note_id=request.note_id,
                    note_url=request.note_url or "",
                    limit=request.limit,
                    cursor=request.cursor,
                    top_level_only=request.top_level_only,
                    context=context,
                )
            )
        else:
            raise ContentResearchValidationError(
                f"unsupported source collection operation: {request.operation}"
            )
        result_payload = asdict(result)
        event_name = (
            "source_collection_completed"
            if result.status in {"completed", "empty"}
            else "source_collection_failed"
        )
        self._append_event(
            trace_id=trace.id,
            workflow_run_id=workflow_run_id,
            thread_id=brief.thread_id,
            sequence_no=sequence_no + 1,
            event_type="task_completed"
            if result.status in {"completed", "empty"}
            else "task_failed",
            event_name=event_name,
            payload={
                "schema_version": "content_research_observation_event_v1",
                "source_collection": result_payload,
            },
        )
        return ContentResearchSourceCollectionResponse(
            workflow_run_id=workflow_run_id,
            provider=result.provider,
            source_kind=result.source_kind,
            operation=result.operation,
            status=result.status,
            failure_reason=result.failure_reason,
            cookie_status=result.cookie_status,
            items=result.items,
            metadata=result.metadata,
            next_cursor=result.next_cursor,
            completeness=result.completeness,
            field_availability=result.field_availability,
            retryable=result.retryable,
        )

    async def _execute_formal_research(
        self,
        *,
        brief: ResearchBriefRecord,
        provider: str,
        source_kind: str,
        limit: int,
    ) -> None:
        tasks = self._store.list_subagent_tasks_for_workflow(brief.workflow_run_id)
        # Failed specialists stay on the same parent Run until the user asks
        # for a retry.  Completed siblings are reused; only the failed work is
        # executed again.
        executable_tasks = [
            task for task in tasks if task.status in {"queued", "pending", "failed"}
        ]
        terminals = await asyncio.gather(
            *(
                self._task_router.execute_task(
                    task,
                    provider=provider,
                    source_kind=source_kind,
                    limit=limit,
                    # Each specialist owns its own query and source result.
                    source_result=None,
                )
                for task in executable_tasks
            )
        )
        terminal_by_id = {task.id: task for task in terminals}
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
        }
        recoverable_failure_by_task = {
            checkpoint.subagent_task_id: str(
                (checkpoint.payload.get("completion") or {}).get("failure_code")
            )
            for checkpoint in self._store.list_typed_records(StageCheckpointRecord)
            if checkpoint.workflow_run_id == brief.workflow_run_id
            and checkpoint.stage_name == "operation"
            and checkpoint.status != "superseded"
            and str(
                (checkpoint.payload.get("completion") or {}).get("failure_code")
                or ""
            )
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
                requires_recovery = terminal.id in recoverable_failure_by_task
                outcomes.append(
                    {
                        "child_task_id": child_id,
                        # An interrupted provider operation has no safe replay
                        # path.  Preserve its richer task status while making
                        # the parent workflow enter its standard retry state.
                        "status": "failed"
                        if terminal.status == "outcome_unknown" or requires_recovery
                        else terminal.status,
                        "error": output.get("error_message")
                        or recoverable_failure_by_task.get(terminal.id),
                        "artifact_refs": self._governed_artifact_refs(
                            workflow_run_id=brief.workflow_run_id,
                            direction_id=str(terminal.direction_id or ""),
                            packet_ids=list((output.get("metadata") or {}).get("packet_ids") or []),
                        ),
                    }
                )
        complete = getattr(self._workflow_runtime, "complete_formal_research", None)
        failed_outcomes = [outcome for outcome in outcomes if outcome["status"] == "failed"]
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
            return

        plans = self._store.list_plans_for_brief(brief.id)
        if not plans:
            raise ContentResearchValidationError(
                f"Formal governance requires a research plan: {brief.workflow_run_id}"
            )
        governance = self._cross_direction_governance.execute(
            workflow_run_id=brief.workflow_run_id,
            research_plan_id=plans[-1].id,
            subagent_task_id=f"governance:{plans[-1].id}",
            action_hypotheses=_requested_action_hypotheses(
                question=str(brief.payload.get("custom_research_question") or ""),
                claim_ids=tuple(_admitted_claim_ids_for_run(self._store, brief.workflow_run_id)),
            ),
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

        if complete is not None:
            artifact_refs = _unique_artifact_refs(
                [ref for outcome in outcomes for ref in outcome["artifact_refs"]] + governance_refs
            )
            await complete(
                workflow_run_id=brief.workflow_run_id,
                task_outcomes=outcomes,
                artifact_refs=artifact_refs,
            )
            # Creator's terminal-run contract is deliberate: only after the
            # workflow reaches a terminal success state may the final report
            # artifact and its single timeline message become visible.
            report_artifact_ref = await self._publish_report_after_workflow_completion(
                workflow_run_id=brief.workflow_run_id,
                thread_id=brief.thread_id,
            )
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

    async def _publish_report_after_workflow_completion(
        self, *, workflow_run_id: str, thread_id: str
    ) -> dict[str, str] | None:
        """Publish only after Creator has recorded the terminal workflow state."""
        async with WorkflowStore(self._store._db_path) as workflow_store:
            run = await workflow_store.get_run(workflow_run_id)
        if run is None or run.status.value != "succeeded":
            return None
        async with ThreadStore(self._store._db_path) as thread_store:
            if await thread_store.get_thread(thread_id) is None:
                raise ContentResearchValidationError(
                    f"Creator thread is required to publish formal report: {thread_id}"
                )
        snapshot_response = self.create_result_snapshot(
            workflow_run_id, result_type="governed_research_report"
        )
        snapshot = next(
            item
            for item in self._store.list_result_snapshots_for_workflow(workflow_run_id)
            if item.id == snapshot_response.snapshot_id
        )
        publication = await self._report_execution.execute(snapshot, self._report_semantic_auditor)
        artifact = await ReportPublicationMaterializer(
            self._store, self._store._db_path
        ).materialize(publication.id)
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

    async def _finalize_hard_cutoff(
        self,
        *,
        request: PresearchInput,
        task: asyncio.Task[PresearchOutcome],
        attempt_id: str,
        trace_id: str,
        sequence_no: int,
    ) -> None:
        outcome = await self._presearch.wait_for_hard_cutoff(request=request, task=task)
        if outcome is None:
            return
        brief = self._store.get_brief_by_presearch_attempt(attempt_id)
        if brief is None:
            return
        if outcome.timeout_status != "final_timeout":
            return
        updated = replace(
            brief,
            status="final_timeout",
            payload={**brief.payload, **self._outcome_payload(outcome), "status": "final_timeout"},
            updated_at=utcnow(),
        )
        self._store.save_brief(updated)
        self._append_presearch_outcome_event(
            trace_id=trace_id,
            workflow_run_id=request.workflow_run_id,
            thread_id=request.thread_id,
            sequence_no=sequence_no,
            outcome=outcome,
            attempt_id=attempt_id,
        )

    def _save_trace(self, trace_id: str, workflow_run_id: str, thread_id: str, now) -> None:
        self._store.save_trace(
            TraceRecord(
                id=trace_id,
                workflow_run_id=workflow_run_id,
                thread_id=thread_id,
                schema_version="content_research_trace_v1",
                status="running",
                started_at=now,
                payload={
                    "schema_version": "content_research_trace_v1",
                    "trace_type": "presearch",
                    "stage": "presearch",
                },
            )
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
        outcome: PresearchOutcome,
    ) -> ResearchBriefRecord:
        payload = {
            "schema_version": "content_research_brief_v1",
            "attempt_id": attempt_id,
            "seed_text": seed_text,
            "user_note": user_note,
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

    def _append_presearch_outcome_event(
        self,
        *,
        trace_id: str,
        workflow_run_id: str,
        thread_id: str,
        sequence_no: int,
        outcome: PresearchOutcome,
        attempt_id: str,
    ) -> None:
        event_name = "presearch_completed"
        if outcome.timeout_status == "first_timeout":
            event_name = "presearch_first_timeout"
        elif outcome.timeout_status == "final_timeout":
            event_name = "presearch_final_timeout"
        elif outcome.fallback_used:
            event_name = "presearch_fallback_used"
        self._append_event(
            trace_id=trace_id,
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            sequence_no=sequence_no,
            event_type="task_completed"
            if outcome.timeout_status != "final_timeout"
            else "task_failed",
            event_name=event_name,
            payload={
                "schema_version": "content_research_observation_event_v1",
                "attempt_id": attempt_id,
                "status": outcome.status,
                "timeout_status": outcome.timeout_status,
                "fallback_used": outcome.fallback_used,
                "error_code": outcome.error_code,
            },
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
    def _outcome_payload(outcome: PresearchOutcome) -> dict:
        checklist = outcome.checklist
        return {
            "status": outcome.status,
            "subject_confirmation": checklist.subject_confirmation,
            "competitor_tags": checklist.competitor_tags,
            "research_directions": checklist.research_directions,
            "custom_research_question": checklist.custom_research_question,
            "custom_competitor_input": checklist.custom_competitor_input,
            "timeout_status": outcome.timeout_status,
            "fallback_used": outcome.fallback_used,
            "error_code": outcome.error_code,
            "error_message": outcome.error_message,
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
    def _response_from_brief(brief: ResearchBriefRecord) -> ContentResearchPresearchResponse:
        payload = brief.payload
        return ContentResearchPresearchResponse(
            attempt_id=str(payload["attempt_id"]),
            workflow_run_id=brief.workflow_run_id,
            brief_id=brief.id,
            status=str(payload.get("status") or brief.status),
            subject_confirmation=str(payload.get("subject_confirmation") or ""),
            competitor_tags=list(payload.get("competitor_tags") or []),
            research_directions=list(payload.get("research_directions") or []),
            custom_research_question=str(payload.get("custom_research_question") or ""),
            custom_competitor_input=str(payload.get("custom_competitor_input") or ""),
            timeout_status=str(payload.get("timeout_status") or "none"),
            fallback_used=bool(payload.get("fallback_used")),
            local_cache_id=brief.id,
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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


def _normalize_missing_evidence(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        if isinstance(value, dict):
            normalized.append(value)
        else:
            normalized.append(
                {
                    "schema_version": "content_research_missing_evidence_v1",
                    "reason": "missing_evidence",
                    "message": str(value),
                    "sequence_no": index,
                }
            )
    return normalized


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


def _evidence_record_view(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "workflow_run_id": record.workflow_run_id,
        "source_type": record.source_type,
        "source_platform": record.source_platform,
        "source_url": record.source_url,
        "source_id": record.source_id,
        "evidence_type": record.evidence_type,
        "title": record.title,
        "text_excerpt": record.text_excerpt,
        "claim": record.claim,
        "metrics": record.metrics,
        "retrieval_query": record.retrieval_query,
        "retrieval_rank": record.retrieval_rank,
        "retrieval_score": record.retrieval_score,
        "metadata": record.metadata,
        "collected_at": record.collected_at.isoformat(),
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
            "evidence_refs": safe_public_projection(card["evidence_refs"]),
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


def _checkpoint_summary(store: Any, workflow_run_id: str) -> dict[str, Any]:
    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id
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
            "policy_scope": governed["policy_scope"],
            "research_plan_id": governed["research_plan_id"],
            "direction_results": governed["direction_results"],
            "claim_ids": [item["claim_candidate_id"] for item in governed["claim_cards"]],
            "weak_signal_ids": [item["weak_signal_id"] for item in governed["weak_signals"]],
            "cross_direction_ids": [
                item["cross_direction_record_id"] for item in governed["cross_direction_records"]
            ],
            "aggregate_ids": [item["aggregate_claim_id"] for item in governed["aggregate_claims"]],
        }
    )


def _admitted_claim_ids_for_run(store: Any, workflow_run_id: str) -> list[str]:
    candidates = {
        item.id
        for item in store.list_typed_records(ClaimCandidateRecord)
        if item.workflow_run_id == workflow_run_id
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
