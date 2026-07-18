"""Content Research application service."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, replace
from typing import Any, Protocol

from app.content_research.advancement import DecisionAdvancementService
from app.content_research.analysis import DirectionalAnalysisLLM, DirectionalAnalysisService
from app.content_research.api_schemas import (
    CONTENT_RESEARCH_API_SCHEMA_VERSION,
    P0_WORKFLOW_ACTIONS,
    ContentResearchBriefConfirmRequest,
    ContentResearchBriefResponse,
    ContentResearchDirectionResponse,
    ContentResearchFormalResearchResponse,
    ContentResearchPlanResponse,
    ContentResearchPresearchResponse,
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
    ResultItem,
    SnapshotResponse,
)
from app.content_research.contracts import build_default_snapshot
from app.content_research.decision_policy import DecisionPolicyService
from app.content_research.decisions import ResearchDecisionService
from app.content_research.evidence import EvidenceBundleService, EvidenceService
from app.content_research.evidence.models import EvidenceBundleRecord, EvidenceRecord
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
from app.content_research.presearch.service import (
    PresearchInput,
    PresearchOutcome,
    PresearchService,
)
from app.content_research.sources import (
    SourceAdapterRegistry,
    SourceCollectionRequest,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.synthesis import synthesize_snapshot
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

    async def complete_brief_and_plan(self, *, workflow_run_id: str, task_specs: list[dict]) -> list[str]: ...

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

    async def complete_formal_research(self, *, workflow_run_id: str, task_outcomes: list[dict], artifact_refs: list[dict]) -> bool: ...


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
                    {"step_name": "brief_confirm", "phase": WorkflowPhase.INTAKE, "max_attempts": 1},
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

    async def complete_brief_and_plan(self, *, workflow_run_id: str, task_specs: list[dict]) -> list[str]:
        async with WorkflowRunManager(self._db_path) as manager:
            await manager.start_step(workflow_run_id, "brief_confirm")
            await manager.complete_step(
                workflow_run_id,
                "brief_confirm",
                artifact_refs=[{"type": "content_research_brief_confirmed"}],
            )
            await manager.advance_to_next_step(workflow_run_id)

            await manager.start_step(workflow_run_id, "plan_build")
            await manager.complete_step(
                workflow_run_id,
                "plan_build",
                artifact_refs=[
                    {"type": "content_research_plan"},
                    {"type": "content_research_subagent_task_specs", "count": len(task_specs)},
                ],
            )
            await manager.advance_to_next_step(workflow_run_id)

            formal_research_step = await manager.start_step(workflow_run_id, "formal_research")
            child_tasks = await manager.create_child_tasks(
                run_id=workflow_run_id,
                step_id=formal_research_step.step_id,
                tasks=[
                    {
                        "task_type": str(spec.get("task_type") or "content_research_source_collect"),
                        "slot_index": index,
                        "checkpoint": spec,
                        "max_attempts": 1,
                    }
                    for index, spec in enumerate(task_specs)
                ],
            )
            return [task.child_task_id for task in child_tasks]

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

    async def complete_formal_research(self, *, workflow_run_id: str, task_outcomes: list[dict], artifact_refs: list[dict]) -> bool:
        snapshot = await self.get_runtime_snapshot(workflow_run_id)
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
                    await manager.retry_child_task(child_id, "retrying failed content research subagent")
                    child_status = "retrying"
                if child_status in {"pending", "retrying"}:
                    await manager.start_child_task(child_id)
                if succeeded:
                    await manager.complete_child_task(child_id, artifact_refs=outcome.get("artifact_refs") or [])
                elif child_status != "failed":
                    await manager.fail_child_task(child_id, outcome.get("error") or "subagent execution failed")
            if any(outcome["status"] == "failed" for outcome in task_outcomes):
                # A failed specialist is visible and retryable, but it cannot
                # silently advance the parent step or expose decision UI.
                return False
            await manager.complete_step(workflow_run_id, "formal_research", artifact_refs=artifact_refs)
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
                cancelled = await manager.cancel_run(workflow_run_id, reason="content_research_ended")
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
        async with ThreadStore(self._db_path) as thread_store:
            await thread_store.delete_thread(thread_id)
        return {
            "schema_version": CONTENT_RESEARCH_API_SCHEMA_VERSION,
            "ended": True,
            "workflow_run_id": workflow_run_id,
            "thread_id": thread_id,
            "active_run_cleared": True,
            "resources_destroyed": True,
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
        analysis_service = DirectionalAnalysisService(llm=analysis_llm, db_path=store._db_path) if analysis_llm is not None else None
        self._task_router = SubagentTaskRouter(store=store, source_registry=self._source_registry, evidence_service=self._evidence_service, bundle_service=self._bundle_service, analysis_service=analysis_service)
        self._decision_service = ResearchDecisionService(store=store, workflow_runtime=workflow_runtime)
        self._decision_advancement_service = DecisionAdvancementService(store=store)
        self._decision_policy_service = DecisionPolicyService(store)

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

        if llm_task is not None and not llm_task.done() and outcome.timeout_status == "first_timeout":
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
        directions = self._direction_registry.require_many(confirmation_request.selected_directions)
        confirmation = BriefConfirmation(
            confirmed_subject=confirmation_request.confirmed_subject.strip(),
            subject_type=confirmation_request.subject_type.strip() or "unknown",
            selected_competitors=_dedupe(confirmation_request.selected_competitors),
            custom_competitors=_dedupe(confirmation_request.custom_competitors),
            selected_directions=confirmation_request.selected_directions,
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
        self._store.save_brief(updated_brief)
        plan = ResearchPlanRecord(
            id=plan_id,
            brief_id=brief.id,
            workflow_run_id=brief.workflow_run_id,
            thread_id=brief.thread_id,
            schema_version="content_research_plan_v1",
            status="draft",
            payload=plan_payload,
        )
        self._store.save_plan(plan)
        snapshot, sample_policies, direction_contracts = build_default_snapshot(
            snapshot_id=_new_id("rps"), workflow_run_id=brief.workflow_run_id,
            brief_id=brief.id, plan_id=plan.id,
        )
        self._store.save_run_policy_snapshot(snapshot)
        for sample_policy in sample_policies:
            self._store.save_sample_policy(sample_policy)
        for direction_contract in direction_contracts:
            self._store.save_direction_contract(direction_contract)

        saved_tasks: list[SubagentTaskRecord] = []
        for index, direction in enumerate(directions):
            self._store.save_direction(
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
                self._store.save_subagent_task(
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
            )

        workflow_child_task_ids = await self._workflow_runtime.complete_brief_and_plan(
            workflow_run_id=brief.workflow_run_id,
            task_specs=[task.payload for task in saved_tasks],
        )
        for task, workflow_child_task_id in zip(saved_tasks, workflow_child_task_ids, strict=False):
            self._store.save_subagent_task(
                replace(
                    task,
                    payload={**task.payload, "workflow_child_task_id": workflow_child_task_id},
                    updated_at=utcnow(),
                )
            )

        return await self.get_workflow_summary(brief.workflow_run_id)

    def get_policy_snapshot(self, workflow_run_id: str) -> dict[str, Any]:
        snapshot = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if snapshot is None:
            raise ContentResearchNotFoundError(f"Policy snapshot not found for workflow: {workflow_run_id}")
        contracts = self._store.list_direction_contracts(snapshot.id)
        policies = [self._store.get_sample_policy(item.sample_policy_id) for item in contracts]
        return {
            "schema_version": "content_research_policy_snapshot_response_v1",
            "id": snapshot.id,
            "workflow_run_id": snapshot.workflow_run_id,
            "effective_policy": snapshot.effective_policy,
            "effective_policy_hash": snapshot.effective_policy_hash,
            "run_as_of_at": snapshot.run_as_of_at.isoformat(),
            "sample_policies": [asdict(item) for item in policies if item is not None],
            "direction_contracts": [asdict(item) for item in contracts],
        }

    async def get_workflow_summary(self, workflow_run_id: str) -> ContentResearchWorkflowSummaryResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
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
                    name=str(item.payload.get("name") or item.payload.get("direction_id") or item.id),
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

    async def list_workflow_events(self, workflow_run_id: str) -> ContentResearchWorkflowEventsResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
        return ContentResearchWorkflowEventsResponse(
            workflow_run_id=workflow_run_id,
            events=await self._workflow_runtime.list_events(workflow_run_id),
        )

    async def get_workflow_trace(self, workflow_run_id: str) -> ContentResearchTraceResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
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
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
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
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
        response = await self._decision_service.submit_decision(
            brief=brief,
            target_type=target_type,
            request=request,
            user_id=user_id,
        )
        if response.idempotent_replay:
            advancement = self._decision_advancement_service.describe(brief=brief, decision=response)
            return response.model_copy(update={"advancement": {**response.advancement, **advancement}})
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
            (item for item in self._store.list_subagent_tasks_for_workflow(workflow_run_id) if item.id == task_id),
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
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
        plans = self._store.list_plans_for_brief(brief.id)
        plan = plans[-1] if plans else None
        existing = self._store.list_result_snapshots_for_workflow(workflow_run_id)
        snapshot_version = str(len(existing) + 1)
        bundles = [
            self._decision_policy_service.apply_to_bundle(bundle)
            for bundle in self._store.list_evidence_bundles_for_workflow(workflow_run_id)
        ]
        result_items = [_result_item_from_bundle(bundle) for bundle in bundles]
        result_items.sort(key=_priority_sort_key)
        result_items = [_with_ranked_decision_card(item, rank) for rank, item in enumerate(result_items, start=1)]

        synthesis = synthesize_snapshot(result_items)
        limitations = synthesis["limitations"]
        abstentions = _snapshot_abstentions(result_items, bundles)
        supported_count = sum(1 for item in result_items if item.evidence_state in {"verified", "partially_supported"})
        unsupported_count = len(result_items) - supported_count
        snapshot = ResearchResultSnapshotRecord(
            id=_new_id("rrs"),
            workflow_run_id=workflow_run_id,
            research_brief_id=brief.id,
            research_plan_id=plan.id if plan else None,
            schema_version="content_research_result_snapshot_v1",
            snapshot_version=snapshot_version,
            result_type=result_type,
            status="ready" if result_items and unsupported_count == 0 else ("partial" if result_items else "partial"),
            title=_snapshot_title(brief, result_type),
            executive_summary=synthesis["executive_summary"],
            findings=[item.model_dump(mode="json") for item in result_items],
            recommendations=synthesis["recommendations"],
            evidence_bundle_ids=[bundle.id for bundle in bundles],
            claim_count=len(result_items),
            supported_claim_count=supported_count,
            unsupported_claim_count=unsupported_count,
            citation_coverage_score=_average_metric(bundles, "citation_coverage", "citation_coverage_score"),
            faithfulness_score=_average_metric(bundles, "faithfulness_metrics", "faithfulness_score"),
            answer_relevancy_score=_average_metric(bundles, "retrieval_metrics", "query_relevance_score"),
            derivation_completeness_score=_derivation_score(result_items),
            evidence_boundary_calibration_score=_evidence_boundary_calibration_score(result_items),
            decision_summary=_decision_summary(result_items),
            decision_cards=[item.decision_card for item in result_items],
            priority_summary=_priority_summary(result_items),
            evidence_boundary_summary=_evidence_boundary_summary(result_items),
            limitations=limitations,
            abstentions=abstentions,
            metadata={
                "schema_version": "content_research_result_snapshot_metadata_v1",
                "bundle_count": len(bundles),
                "created_from": "evidence_bundles",
                "synthesis_version": "content_research_main_synthesis_v1",
            },
        )
        saved = self._store.save_result_snapshot(snapshot)
        return self._snapshot_response(saved)

    def get_workflow_results(self, workflow_run_id: str) -> SnapshotResponse:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
        snapshots = self._store.list_result_snapshots_for_workflow(workflow_run_id)
        if not snapshots:
            return self.create_result_snapshot(workflow_run_id)
        # Focus snapshots record selected follow-up work. The regular Creator
        # result surface must keep serving the research snapshot until the
        # deep-research completion flow explicitly promotes a final insight.
        visible = [snapshot for snapshot in snapshots if snapshot.result_type != "final_insight_focus"]
        return self._snapshot_response(visible[-1] if visible else snapshots[-1])

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
            raise ContentResearchValidationError(f"Unsupported Content Research workflow action: {action}")

        brief = self._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")

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
            result = await self._workflow_runtime.pause_content_research_run(workflow_run_id=workflow_run_id)
            return self._action_response(workflow_run_id=workflow_run_id, action=action, status=result["status"], result=result, local_cache_id=brief.id)

        if action == "resume_formal_research":
            result = await self._workflow_runtime.resume_content_research_run(workflow_run_id=workflow_run_id)
            return self._action_response(workflow_run_id=workflow_run_id, action=action, status=result["status"], result=result, local_cache_id=brief.id)

        if action not in {"start_formal_research", "retry_formal_research"}:
            raise ContentResearchValidationError(f"Unsupported Content Research workflow action: {action}")
        source_request = ContentResearchSourceCollectionRequest(**request.payload)
        formal_result = await self.start_formal_research(workflow_run_id=workflow_run_id, request=source_request)
        return self._action_response(
            workflow_run_id=workflow_run_id,
            action=action,
            status=formal_result.status,
            result=formal_result.model_dump(mode="json"),
            local_cache_id=brief.id,
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
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
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
            if task.status == "failed"
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
            raise ContentResearchNotFoundError(f"Content research workflow not found: {workflow_run_id}")
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
                "source_kind": request.source_kind,
                "provider": request.provider,
                "query": query,
                "limit": request.limit,
                "sort": request.sort,
            },
        )
        result = await self._source_registry.get(request.provider).collect(
            SourceCollectionRequest(
                workflow_run_id=workflow_run_id,
                query=query,
                source_kind=request.source_kind,
                limit=request.limit,
                sort=request.sort,
                context={"thread_id": brief.thread_id, "brief_id": brief.id},
            )
        )
        result_payload = asdict(result)
        event_name = "source_collection_completed" if result.status in {"completed", "empty"} else "source_collection_failed"
        self._append_event(
            trace_id=trace.id,
            workflow_run_id=workflow_run_id,
            thread_id=brief.thread_id,
            sequence_no=sequence_no + 1,
            event_type="task_completed" if result.status in {"completed", "empty"} else "task_failed",
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
            status=result.status,
            failure_reason=result.failure_reason,
            cookie_status=result.cookie_status,
            items=result.items,
            metadata=result.metadata,
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
        executable_tasks = [task for task in tasks if task.status in {"queued", "pending", "failed"}]
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
        outcomes: list[dict] = []
        for task in tasks:
            terminal = terminal_by_id.get(task.id) or task
            if terminal.status not in {"completed", "partial_completed", "failed"}:
                raise ContentResearchValidationError(
                    f"Subagent task did not reach a terminal state: {terminal.id} ({terminal.status})"
                )
            output = dict(terminal.payload.get("output_payload") or {})
            child_id = str(terminal.payload.get("workflow_child_task_id") or "")
            if child_id:
                bundle_id = str(output.get("evidence_bundle_id") or "")
                outcomes.append({
                    "child_task_id": child_id,
                    "status": terminal.status,
                    "error": output.get("error_message"),
                    "artifact_refs": [{"type": "content_research_evidence_bundle", "id": bundle_id}] if bundle_id else [],
                })
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
                    "failed_subagent_task_ids": [outcome["child_task_id"] for outcome in failed_outcomes],
                    "message": "One or more research specialists failed; retry is required before results can be finalized.",
                },
            )
            return

        snapshot = self.create_result_snapshot(brief.workflow_run_id)
        if complete is not None:
            await complete(
                workflow_run_id=brief.workflow_run_id,
                task_outcomes=outcomes,
                artifact_refs=[
                    {"type": "content_research_result_snapshot", "id": snapshot.snapshot_id},
                ],
            )

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
            event_type="task_completed" if outcome.timeout_status != "final_timeout" else "task_failed",
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
            items=[ResultItem(**item) for item in snapshot.findings],
            findings=snapshot.findings,
            recommendations=snapshot.recommendations,
            evidence_bundle_ids=snapshot.evidence_bundle_ids,
            claim_count=snapshot.claim_count,
            supported_claim_count=snapshot.supported_claim_count,
            unsupported_claim_count=snapshot.unsupported_claim_count,
            citation_coverage_score=snapshot.citation_coverage_score,
            faithfulness_score=snapshot.faithfulness_score,
            answer_relevancy_score=snapshot.answer_relevancy_score,
            derivation_completeness_score=snapshot.derivation_completeness_score,
            evidence_boundary_calibration_score=snapshot.evidence_boundary_calibration_score,
            decision_summary=snapshot.decision_summary,
            decision_cards=snapshot.decision_cards,
            priority_summary=snapshot.priority_summary,
            evidence_boundary_summary=snapshot.evidence_boundary_summary,
            limitations=snapshot.limitations,
            abstentions=snapshot.abstentions,
            metadata=snapshot.metadata,
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


def _result_item_from_bundle(bundle: EvidenceBundleRecord) -> ResultItem:
    card = bundle.decision_card or DecisionPolicyService().build_decision_card(bundle)
    priority = dict(card.get("priority") or {})
    evidence = dict(card.get("evidence") or {})
    claim_scope = dict(card.get("claim_scope") or {})
    next_action = dict(card.get("next_action") or {})
    evidence_state = str(evidence.get("state") or bundle.evidence_state or "signal")
    evidence_grade = str(evidence.get("grade") or bundle.evidence_grade or "C")
    priority_label = str(priority.get("label") or bundle.priority.get("label") or "do_not_prioritize")
    source_count = int(bundle.coverage.get("source_count") or bundle.coverage.get("accepted_evidence_count") or 0)
    missing_evidence = _normalize_missing_evidence(evidence.get("missing_evidence") or bundle.missing_evidence)
    claim_status = _claim_status(evidence_state)
    risk_flags = _risk_flags(bundle, evidence_state, missing_evidence)
    return ResultItem(
        result_item_id=f"ri_{bundle.id}",
        claim=bundle.summary,
        summary=bundle.summary,
        evidence_bundle_id=bundle.id,
        evidence_bundle_ids=[bundle.id],
        support_level=_support_level(evidence_state),
        claim_status=claim_status,
        priority=priority,
        priority_label=priority_label,
        evidence_state=evidence_state,
        evidence_grade=evidence_grade,
        claim_scope=claim_scope,
        next_action=next_action,
        decision_card=card,
        risk_flags=risk_flags,
        missing_evidence=missing_evidence,
        source_count=source_count,
    )


def _priority_sort_key(item: ResultItem) -> tuple[int, str]:
    order = {
        "high_priority": 0,
        "high_potential_needs_more_evidence": 1,
        "evidence_backed_reference": 2,
        "useful_but_lower_priority": 3,
        "do_not_prioritize": 4,
    }
    return (order.get(item.priority_label, 9), item.result_item_id)


def _with_ranked_decision_card(item: ResultItem, rank: int) -> ResultItem:
    priority = dict(item.priority)
    priority["rank"] = rank
    card = dict(item.decision_card)
    card["priority"] = priority
    return item.model_copy(update={"priority": priority, "decision_card": card})


def _claim_status(evidence_state: str) -> str:
    if evidence_state == "invalid":
        return "unsupported"
    if evidence_state in {"signal", "case_only"}:
        return "evidence_insufficient"
    return "supported"


def _support_level(evidence_state: str) -> str:
    if evidence_state == "invalid":
        return "unsupported"
    if evidence_state == "verified":
        return "high"
    if evidence_state == "partially_supported":
        return "medium"
    return "signal"


def _risk_flags(
    bundle: EvidenceBundleRecord,
    evidence_state: str,
    missing_evidence: list[dict[str, Any]],
) -> list[str]:
    flags: list[str] = []
    if evidence_state == "invalid":
        flags.append("unsupported_claim")
    if evidence_state in {"signal", "case_only"}:
        flags.append("evidence_insufficient")
    if missing_evidence:
        flags.append("missing_evidence")
    if bundle.unsupported_claim_count > 0:
        flags.append("unsupported_claim_count")
    return flags


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


def _snapshot_limitations(result_items: list[ResultItem], bundles: list[EvidenceBundleRecord]) -> list[dict[str, Any]]:
    if not bundles:
        return [
            {
                "schema_version": "content_research_result_limitation_v1",
                "reason": "no_evidence_bundles",
                "message": "No evidence bundles are available for this workflow yet.",
            }
        ]
    limitations: list[dict[str, Any]] = []
    for item in result_items:
        if item.claim_status != "supported":
            limitations.append(
                {
                    "schema_version": "content_research_result_limitation_v1",
                    "result_item_id": item.result_item_id,
                    "evidence_bundle_id": item.evidence_bundle_id,
                    "reason": item.claim_status,
                    "risk_flags": item.risk_flags,
                }
            )
    return limitations


def _snapshot_abstentions(result_items: list[ResultItem], bundles: list[EvidenceBundleRecord]) -> list[dict[str, Any]]:
    if bundles:
        return [
            {
                "schema_version": "content_research_result_abstention_v1",
                "result_item_id": item.result_item_id,
                "evidence_bundle_id": item.evidence_bundle_id,
                "reason": item.claim_status,
            }
            for item in result_items
            if item.claim_status == "unsupported"
        ]
    return [
        {
            "schema_version": "content_research_result_abstention_v1",
            "reason": "no_supported_claims",
            "message": "The workflow has no evidence-backed result items yet.",
        }
    ]


def _snapshot_title(brief: ResearchBriefRecord, result_type: str) -> str:
    subject = str(
        brief.payload.get("seed_text")
        or brief.payload.get("confirmed_subject")
        or brief.payload.get("subject_confirmation")
        or "本轮调研"
    ).strip()
    return f"{subject} 内容调研"


def _snapshot_summary(items: list[ResultItem]) -> str:
    if not items:
        return "暂时没有可展示的证据结论，请先完成内容采集。"
    supported = [item for item in items if item.evidence_state in {"verified", "partially_supported"}]
    if supported:
        return supported[0].summary
    return "当前线索的证据仍不足，建议查看限制说明后再决定是否采用。"


def _snapshot_recommendations(items: list[ResultItem]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for item in items:
        if item.priority_label == "high_priority":
            action = "Use this finding as a candidate input for the next research decision."
        else:
            action = "Collect more evidence before treating this signal as a conclusion."
        recommendations.append(
            {
                "schema_version": "content_research_recommendation_v1",
                "recommendation_id": f"rec_{item.result_item_id}",
                "action": item.next_action.get("proposal") or action,
                "action_type": item.next_action.get("type") or "review",
                "based_on_findings": [item.result_item_id],
                "evidence_bundle_ids": item.evidence_bundle_ids,
            }
        )
    return recommendations


def _decision_summary(items: list[ResultItem]) -> dict[str, Any]:
    return {
        "schema_version": "content_research_decision_summary_v1",
        "item_count": len(items),
        "priority": _priority_summary(items),
        "evidence_boundary": _evidence_boundary_summary(items),
    }


def _evidence_boundary_summary(items: list[ResultItem]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.evidence_state] = counts.get(item.evidence_state, 0) + 1
    return {
        "schema_version": "content_research_evidence_boundary_summary_v1",
        "states": counts,
    }


def _priority_summary(items: list[ResultItem]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.priority_label] = counts.get(item.priority_label, 0) + 1
    return {
        "schema_version": "content_research_priority_summary_v1",
        "item_count": len(items),
        "labels": counts,
    }


def _average_metric(bundles: list[EvidenceBundleRecord], attr_name: str, metric_name: str) -> float | None:
    values = []
    for bundle in bundles:
        source = getattr(bundle, attr_name)
        if isinstance(source, dict) and metric_name in source:
            value = _optional_float(source.get(metric_name))
            if value is not None:
                values.append(value)
    return sum(values) / len(values) if values else None


def _evidence_boundary_calibration_score(items: list[ResultItem]) -> float | None:
    if not items:
        return None
    supported = sum(1 for item in items if item.evidence_state in {"verified", "partially_supported"})
    return supported / len(items)


def _derivation_score(items: list[ResultItem]) -> float | None:
    if not items:
        return None
    supported = sum(1 for item in items if item.claim_status == "supported")
    return supported / len(items)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
