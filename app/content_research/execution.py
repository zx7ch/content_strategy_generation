"""Worker execution seam for Content Research.

The interface owns claim-scoped orchestration and terminal failure projection.
Lifecycle transitions, domain repositories, and lease validation retain their
existing authorities and transaction boundaries.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from app.content_research.analysis_persistence import (
    AnalysisJobClaim,
    SQLiteMarketingAnalysisRepository,
)
from app.content_research.api_schemas import (
    ContentResearchFormalResearchResponse,
    ContentResearchSourceCollectionRequest,
)
from app.content_research.errors import (
    ContentResearchNotFoundError,
    ContentResearchValidationError,
)
from app.content_research.lifecycle.coordinator import LifecycleCommandConflict
from app.content_research.lifecycle.models import (
    ContentResearchState,
    LifecycleCommand,
)
from app.content_research.marketing_analysis_execution import (
    MarketingAnalysisExecutionService,
)
from app.content_research.models import SubagentTaskRecord, utcnow
from app.content_research.persistence_models import ReportPublicationRecord
from app.content_research.runtime import canonical_fingerprint
from app.content_research.scope_contract import (
    DispatchLeaseContext,
    ExecutionContext,
    ExecutionLeaseFencedError,
    ScopeExecutionAttempt,
    ScopeExecutionContinuation,
)
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore

if TYPE_CHECKING:
    from app.content_research.service import ContentResearchService


class ContentResearchExecution(Protocol):
    """Worker-visible claim execution and failure projection interface."""

    async def execute_claimed_dispatch(
        self,
        *,
        context: DispatchLeaseContext,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchFormalResearchResponse: ...

    async def record_dispatch_failure(
        self,
        workflow_run_id: str,
        error: BaseException | str,
    ) -> None: ...

    async def execute_execution_unit(
        self,
        claim: ScopeExecutionAttempt,
        continuation: ScopeExecutionContinuation,
    ) -> str: ...

    async def execute_scope_continuation(
        self,
        continuation: ScopeExecutionContinuation,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> None: ...

    async def execute_claimed_analysis(self, claim: AnalysisJobClaim) -> None: ...

    async def record_analysis_failure(
        self,
        workflow_run_id: str,
        error: BaseException | str,
        *,
        attempt_id: str | None = None,
        lease_token: str | None = None,
        allow_expired_lease: bool = False,
    ) -> None: ...

    async def record_report_finalization_failure(
        self,
        workflow_run_id: str,
        error: BaseException | str,
    ) -> None: ...


class ContentResearchExecutionService:
    """Claim-scoped implementation used by dispatch and analysis workers."""

    def __init__(self, source: ContentResearchService) -> None:
        self._application = source

    async def execute_claimed_dispatch(
        self,
        *,
        context: DispatchLeaseContext,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchFormalResearchResponse:
        """Execute a normal dispatch through a store view fenced to its exact claim."""
        application = self._application
        if not application._store.dispatch_context_is_live(context):
            raise ExecutionLeaseFencedError("dispatch lease was fenced before formal research")
        bind_runtime = getattr(application._workflow_runtime, "for_dispatch_context", None)
        scoped_runtime = (
            bind_runtime(context) if callable(bind_runtime) else application._workflow_runtime
        )
        from app.content_research.service import ContentResearchService

        scoped_application = ContentResearchService(
            # Provider/evidence writes are fenced by the explicit dispatch
            # context passed into the async pipeline below. Binding every
            # synchronous read to BEGIN IMMEDIATE can deadlock the event loop
            # against the async lifecycle writer before retrieval even starts.
            store=application._store,
            presearch=application._presearch,
            workflow_runtime=scoped_runtime,
            source_registry=application._source_registry,
            analysis_llm=application._analysis_llm,
            report_semantic_auditor=application._report_semantic_auditor,
            dispatch_wake_event=application._dispatch_wake_event,
            analysis_wake_event=application._analysis_wake_event,
            research_embedding_runtime=application._research_embedding_runtime,
        )
        return await scoped_application._execution_interface._start_formal_research_for_dispatch(
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
        application = self._application
        brief = application._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        await application._advance_lifecycle_if_current(
            workflow_run_id,
            expected_state=ContentResearchState.RETRIEVAL_QUEUED,
            event="worker_claimed",
        )
        application._require_scope_execution_authority(workflow_run_id=workflow_run_id)
        async with ThreadStore(application._store._db_path, read_only=True) as thread_store:
            if await thread_store.get_thread(brief.thread_id) is None:
                raise ContentResearchValidationError(
                    "Content research cannot start because its Creator thread no longer exists. "
                    "Create a new checklist from an active Creator conversation."
                )
        await application._execute_formal_research(
            brief=brief,
            provider=request.provider,
            source_kind=request.source_kind,
            limit=request.limit,
            dispatch_context=dispatch_context,
        )
        tasks = application._store.list_subagent_tasks_for_workflow(workflow_run_id)
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
            partial_completed_task_count=sum(task.status == "partial_completed" for task in tasks),
            failed_tasks=failed_tasks,
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )

    async def record_dispatch_failure(
        self,
        workflow_run_id: str,
        error: BaseException | str,
    ) -> None:
        application = self._application
        current = await application._lifecycle.load(workflow_run_id)
        if current.state in {
            ContentResearchState.REPORT_READY,
            ContentResearchState.RECOVERY_REQUIRED,
            ContentResearchState.CANCELLED_OR_FAILED,
        }:
            return
        if current.state is ContentResearchState.REPORT_COMPOSING:
            await self.record_report_finalization_failure(workflow_run_id, error)
            return
        message = str(error) or "Content research dispatch failed"
        await application._lifecycle.apply(
            LifecycleCommand(
                command_id=f"dispatch-failed:{workflow_run_id}:{current.state_revision}",
                run_id=workflow_run_id,
                expected_state=current.state,
                expected_revision=current.state_revision,
                kind="fail",
                payload={
                    "error": {
                        "code": "FORMAL_RESEARCH_DISPATCH_FAILED",
                        "stage": current.state.value,
                        "operation": "formal_research_dispatch",
                        "message": message,
                        "retryable": True,
                        "recovery_action": "retry_retrieval",
                    }
                },
            )
        )

    async def record_report_finalization_failure(
        self,
        workflow_run_id: str,
        error: BaseException | str,
    ) -> None:
        """Preserve completed retrieval/analysis and expose report-only recovery."""
        application = self._application
        current = await application._lifecycle.load(workflow_run_id)
        if current.state in {
            ContentResearchState.REPORT_READY,
            ContentResearchState.RECOVERY_REQUIRED,
            ContentResearchState.CANCELLED_OR_FAILED,
        }:
            return
        if current.state is not ContentResearchState.REPORT_COMPOSING:
            raise LifecycleCommandConflict("report finalization failure requires report_composing")
        effective_attempt = await asyncio.to_thread(
            SQLiteMarketingAnalysisRepository(
                application._store._db_path,
                bootstrap_schema=False,
                writer=application._store._writer,
            ).get_effective_attempt_for_run,
            workflow_run_id,
        )
        message = str(error) or "Report finalization failed"
        await application._lifecycle.apply(
            LifecycleCommand(
                command_id=(
                    f"report-finalization-failed:{workflow_run_id}:{current.state_revision}"
                ),
                run_id=workflow_run_id,
                expected_state=current.state,
                expected_revision=current.state_revision,
                kind="fail",
                payload={
                    "error": {
                        "code": "REPORT_FINALIZATION_FAILED",
                        "stage": "report_composing",
                        "operation": "report_finalization",
                        "message": message,
                        "retryable": True,
                        "recovery_action": "retry_report",
                        "preserved_analysis_attempt_id": (
                            effective_attempt.id
                            if effective_attempt is not None
                            and effective_attempt.state == "succeeded"
                            else None
                        ),
                    }
                },
            )
        )

    async def execute_execution_unit(
        self,
        claim: ScopeExecutionAttempt,
        continuation: ScopeExecutionContinuation,
    ) -> str:
        """Execute one continuation only through its exact live attempt lease."""
        application = self._application
        unit = application._store.get_scope_execution_unit(claim.execution_unit_id)
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
        application._require_live_execution_context(context, "execute_execution_unit")
        bind_runtime = getattr(application._workflow_runtime, "for_execution_context", None)
        scoped_runtime = (
            bind_runtime(context) if callable(bind_runtime) else application._workflow_runtime
        )
        from app.content_research.service import ContentResearchService

        scoped_application = ContentResearchService(
            store=application._store.for_execution_context(context),
            presearch=application._presearch,
            workflow_runtime=scoped_runtime,
            source_registry=application._source_registry,
            analysis_llm=application._analysis_llm,
            report_semantic_auditor=application._report_semantic_auditor,
            dispatch_wake_event=application._dispatch_wake_event,
            analysis_wake_event=application._analysis_wake_event,
            research_embedding_runtime=application._research_embedding_runtime,
        )
        await scoped_application.execution_interface.execute_scope_continuation(
            continuation,
            execution_context=context,
        )
        authorization = application._store.get_scope_execution_authorization(
            continuation.authorization_id
        )
        if authorization is None:
            raise ContentResearchValidationError(
                "execution unit authorization disappeared before completion"
            )
        if continuation.operation == "supplementary_collection":
            terminal = application._store.get_coverage_snapshot(
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
                for fact in application._store.execution_trace(context.execution_unit_id)
                if fact.kind == "publication_persisted"
                and isinstance(fact.payload.get("publication_id"), str)
            ]
            publication_id = (
                str(publication_facts[-1].payload["publication_id"]) if publication_facts else ""
            )
            publication = (
                application._store.get_typed_record(ReportPublicationRecord, publication_id)
                if publication_id
                else None
            )
            async with WorkflowStore(application._store._db_path) as workflow_store:
                artifacts = await workflow_store.list_artifacts(continuation.workflow_run_id)
            materialized = any(
                (artifact.payload_json or {}).get("report_publication_id") == publication_id
                for artifact in artifacts
            )
            analysis_repository = SQLiteMarketingAnalysisRepository(
                application._store._db_path,
                bootstrap_schema=False,
                writer=application._store._writer,
            )
            effective_attempt = analysis_repository.get_effective_attempt_for_run(
                continuation.workflow_run_id
            )
            analysis_context = (
                analysis_repository.get_analysis_job_context(
                    effective_attempt.analysis_unit_id
                )
                if effective_attempt is not None
                else None
            )
            analysis_handoff_committed = bool(
                analysis_context is not None
                and analysis_context.execution_authorization_id == authorization.id
                and effective_attempt is not None
                and effective_attempt.state in {"queued", "running", "succeeded"}
            )
            if not analysis_handoff_committed and (
                publication is None
                or publication.workflow_run_id != continuation.workflow_run_id
                or not materialized
            ):
                raise ContentResearchValidationError(
                    "execution unit limited report has no durable analysis handoff or publication"
                )
        application._require_live_execution_context(context, "execution_terminal_postcondition")
        return "completed"

    async def execute_scope_continuation(
        self,
        continuation: ScopeExecutionContinuation,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> None:
        """Execute only the work owned by one persisted authorization command."""
        application = self._application
        application._require_live_execution_context(execution_context, "scope_continuation_start")
        authorization = application._store.get_scope_execution_authorization(
            continuation.authorization_id
        )
        if authorization is None:
            raise ContentResearchValidationError(
                "scope execution continuation authorization was not found"
            )
        persisted_continuation = next(
            (
                item
                for item in application._store.list_scope_execution_continuations(
                    authorization.workflow_run_id
                )
                if item.authorization_id == authorization.id
            ),
            None,
        )

        def immutable_command(
            item: ScopeExecutionContinuation,
        ) -> tuple[object, ...]:
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
        if continuation.operation == "supplementary_collection":
            await application._advance_lifecycle_if_current(
                continuation.workflow_run_id,
                expected_state=ContentResearchState.RETRIEVAL_QUEUED,
                event="worker_claimed",
            )
        application._require_scope_execution_authority(
            workflow_run_id=continuation.workflow_run_id,
            execution_authorization=authorization,
        )
        brief = application._store.get_brief_by_workflow(continuation.workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {continuation.workflow_run_id}"
            )
        runtime_snapshot = await application._workflow_runtime.get_runtime_snapshot(
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
            restart = getattr(
                application._workflow_runtime,
                "restart_formal_research_step",
                None,
            )
            if callable(restart):
                application._require_live_execution_context(
                    execution_context, "restart_formal_research"
                )
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
                    for task in application._store.list_subagent_tasks_for_workflow(
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
            # immutable evidence. An exact replay uses an authorization-owned
            # attempt namespace instead of mutating that historical evidence.
            prior_attempts = [
                task
                for task in application._store.list_subagent_tasks_for_workflow(
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
            existing_task = application._store.get_subagent_task(task_id)
            if existing_task is None:
                application._require_live_execution_context(
                    execution_context, "create_continuation_task"
                )
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
                application._store.save_subagent_task(existing_task)
            executable_task_ids.add(existing_task.id)

        await application._execute_formal_research(
            brief=brief,
            provider="xiaohongshu",
            source_kind="search_result",
            limit=50,
            execution_authorization=authorization,
            executable_task_ids=executable_task_ids,
            execution_context=execution_context,
        )

    async def execute_claimed_analysis(self, claim: AnalysisJobClaim) -> None:
        """Execute one claimed analysis attempt, then resume report finalization."""
        application = self._application
        brief = application._store.get_brief_by_workflow(claim.context.workflow_run_id)
        if brief is None:
            raise ContentResearchValidationError("Marketing analysis requires the run brief")
        await application._workflow_runtime.restart_formal_research_step(
            workflow_run_id=claim.context.workflow_run_id,
            child_task_ids=[],
        )
        await MarketingAnalysisExecutionService(
            store=application._store,
            llm=application._analysis_llm,
            embedding_runtime=application._research_embedding_runtime,
            llm_scope={
                "llm_scope": {
                    "workspace_id": str(brief.payload.get("workspace_id") or ""),
                    "user_id": str(brief.payload.get("user_id") or ""),
                }
            },
        ).execute_claimed(claim)
        authorization = (
            application._store.get_scope_execution_authorization(
                claim.context.execution_authorization_id
            )
            if claim.context.execution_authorization_id is not None
            else None
        )
        if claim.context.execution_authorization_id is not None and authorization is None:
            raise ContentResearchValidationError(
                "marketing analysis continuation authorization disappeared"
            )
        await application._execute_formal_research(
            brief=brief,
            provider="xiaohongshu",
            source_kind="search_result",
            limit=50,
            execution_authorization=authorization,
        )

    async def record_analysis_failure(
        self,
        workflow_run_id: str,
        error: BaseException | str,
        *,
        attempt_id: str | None = None,
        lease_token: str | None = None,
        allow_expired_lease: bool = False,
    ) -> None:
        """Project a terminal analysis failure through the canonical lifecycle authority."""
        application = self._application
        current = await application._lifecycle.load(workflow_run_id)
        if current.state in {
            ContentResearchState.REPORT_READY,
            ContentResearchState.CANCELLED_OR_FAILED,
        }:
            return
        if current.state is not ContentResearchState.RECOVERY_REQUIRED:
            command = LifecycleCommand(
                command_id=(f"analysis-failed:{workflow_run_id}:{current.state_revision}"),
                run_id=workflow_run_id,
                expected_state=current.state,
                expected_revision=current.state_revision,
                kind="fail",
                payload={
                    "error": {
                        "code": "MARKETING_ANALYSIS_FAILED",
                        "stage": "marketing_analysis",
                        "operation": "marketing_analysis",
                        "message": str(error) or "Marketing analysis failed",
                        "retryable": True,
                        "recovery_action": "retry_analysis",
                        "attempt_id": attempt_id,
                    }
                },
            )
            if attempt_id is not None:
                await application._lifecycle.fail_analysis_attempt(
                    command,
                    attempt_id=attempt_id,
                    lease_token=lease_token,
                    allow_expired_lease=allow_expired_lease,
                )
            else:
                await application._lifecycle.apply(command)
