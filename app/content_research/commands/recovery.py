"""Stage-specific recovery workflow actions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar

from app.content_research.analysis_persistence import SQLiteMarketingAnalysisRepository
from app.content_research.api_schemas import (
    ContentResearchSourceCollectionRequest,
    ContentResearchWorkflowActionResponse,
)
from app.content_research.commands.dispatcher import WorkflowActionContext
from app.content_research.lifecycle.coordinator import LifecycleCommandConflict
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.content_research.marketing_analysis_execution import (
    MarketingAnalysisExecutionService,
)


def _require_recovery_state(context: WorkflowActionContext, action: str) -> ContentResearchState:
    declared_state = ContentResearchState(context.request.expected_state)
    if declared_state is not ContentResearchState.RECOVERY_REQUIRED:
        raise LifecycleCommandConflict(f"{action} requires expected_state recovery_required")
    return declared_state


@dataclass(frozen=True)
class RetryPresearchHandler:
    action: ClassVar[str] = "retry_presearch"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        context.require_brief()
        declared_state = _require_recovery_state(context, self.action)
        request = context.request
        response = await context.command_service.retry_presearch(
            context.workflow_run_id,
            command_id=request.command_id,
            expected_state=declared_state,
            expected_revision=request.expected_revision,
            recovery_plan_id=str(request.payload.get("recovery_plan_id") or ""),
            plan_fingerprint=str(request.payload.get("plan_fingerprint") or ""),
        )
        return context.application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status=response.status,
            result=response.model_dump(mode="json"),
            local_cache_id=response.brief_id,
        )


@dataclass(frozen=True)
class RetryRetrievalHandler:
    action: ClassVar[str] = "retry_retrieval"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        context.require_brief()
        request = context.request
        declared_state = _require_recovery_state(context, self.action)
        command = LifecycleCommand(
            command_id=request.command_id,
            run_id=context.workflow_run_id,
            expected_state=declared_state,
            expected_revision=request.expected_revision,
            kind=self.action,
            payload=request.payload,
        )
        current = await application._lifecycle.load(context.workflow_run_id)
        if (
            current.state is ContentResearchState.RECOVERY_REQUIRED
            and self.action not in current.allowed_actions
        ):
            raise LifecycleCommandConflict("retry_retrieval is not available for this recovery")
        runtime_snapshot = await application._workflow_runtime.get_runtime_snapshot(
            context.workflow_run_id
        )
        provider = str(request.payload.get("provider") or "xiaohongshu")
        source_kind = str(request.payload.get("source_kind") or "search_result")
        limit = int(request.payload.get("limit") or 50)
        workflow_children = list(runtime_snapshot.get("child_tasks") or [])
        if current.state is ContentResearchState.RECOVERY_REQUIRED:
            recovery_child_ids = application._requeue_recoverable_tasks(
                context.workflow_run_id,
                provider=provider,
                workflow_child_states=workflow_children,
                apply_changes=False,
            )
        else:
            failed_runtime_child_ids = {
                str(child.get("child_task_id") or "")
                for child in workflow_children
                if str(child.get("status") or "") == "failed"
            }
            recovery_child_ids = [
                child_id
                for task in application._store.list_subagent_tasks_for_workflow(
                    context.workflow_run_id
                )
                if task.status == "queued"
                and (child_id := str(task.payload.get("workflow_child_task_id") or ""))
                in failed_runtime_child_ids
            ]
        retried = await application._lifecycle.apply(command)
        if current.state is ContentResearchState.RECOVERY_REQUIRED:
            application._requeue_recoverable_tasks(
                context.workflow_run_id,
                provider=provider,
                workflow_child_states=workflow_children,
            )
        await application._workflow_runtime.restart_formal_research_step(
            workflow_run_id=context.workflow_run_id,
            child_task_ids=recovery_child_ids,
        )
        dispatched = await application.dispatch_formal_research(
            workflow_run_id=context.workflow_run_id,
            request=ContentResearchSourceCollectionRequest(
                provider=provider,
                source_kind=source_kind,
                limit=limit,
            ),
            retry_completed=True,
        )
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status=dispatched.status,
            result={"run": application._run_projection_payload(retried)},
            local_cache_id=retried.brief_id,
        )


@dataclass(frozen=True)
class RetryAnalysisHandler:
    action: ClassVar[str] = "retry_analysis"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        brief = context.require_brief()
        request = context.request
        declared_state = _require_recovery_state(context, self.action)
        repository = SQLiteMarketingAnalysisRepository(
            application._store._db_path,
            bootstrap_schema=False,
            writer=application._store._writer,
        )
        predecessor = await asyncio.to_thread(
            repository.get_effective_attempt_for_run,
            context.workflow_run_id,
        )
        if predecessor is None:
            raise LifecycleCommandConflict("legacy run has no retryable analysis attempt")
        unit = await MarketingAnalysisExecutionService(
            store=application._store,
            llm=application._analysis_llm,
            embedding_runtime=application._research_embedding_runtime,
            llm_scope={
                "llm_scope": {
                    "workspace_id": str(brief.payload.get("workspace_id") or ""),
                    "user_id": str(brief.payload.get("user_id") or ""),
                }
            },
        ).assert_retry_compatible(predecessor.analysis_unit_id)
        retried, successor_id = await application._lifecycle.retry_analysis(
            LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                kind=self.action,
                payload={
                    "recovery_plan_id": request.payload.get("recovery_plan_id"),
                    "plan_fingerprint": request.payload.get("plan_fingerprint"),
                    "predecessor_attempt_id": predecessor.id,
                    "analysis_contract_fingerprint": unit.contract_fingerprint,
                },
            ),
            expected_attempt_id=predecessor.id,
            expected_contract_fingerprint=unit.contract_fingerprint,
        )
        if application._analysis_wake_event is not None:
            application._analysis_wake_event.set()
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="queued",
            result={
                "analysis_attempt_id": successor_id,
                "run": application._run_projection_payload(retried),
            },
            local_cache_id=brief.id,
        )


@dataclass(frozen=True)
class RetryReportHandler:
    action: ClassVar[str] = "retry_report"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        brief = context.require_brief()
        request = context.request
        declared_state = _require_recovery_state(context, self.action)
        current = await application._lifecycle.load(context.workflow_run_id)
        if current.error is None or (
            current.error.get("code") != "REPORT_FINALIZATION_FAILED"
            and current.error.get("stage") != ContentResearchState.REPORT_COMPOSING.value
        ):
            raise LifecycleCommandConflict("retry_report requires a report finalization failure")
        retried = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                kind=self.action,
                payload={
                    "recovery_plan_id": request.payload.get("recovery_plan_id"),
                    "plan_fingerprint": request.payload.get("plan_fingerprint"),
                    "preserved_analysis_attempt_id": current.error.get(
                        "preserved_analysis_attempt_id"
                    )
                },
            )
        )
        await application._dispatch.enqueue(
            workflow_run_id=context.workflow_run_id,
            provider="xiaohongshu",
            source_kind="search_result",
            limit=50,
            retry_completed=True,
        )
        if application._dispatch_wake_event is not None:
            application._dispatch_wake_event.set()
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="queued",
            result={
                "run": application._run_projection_payload(retried),
                "reused_retrieval": True,
                "reused_analysis_attempt_id": current.error.get("preserved_analysis_attempt_id"),
            },
            local_cache_id=brief.id,
        )
