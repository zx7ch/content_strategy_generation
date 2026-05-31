"""Workflow step executors.

Executors consume structured StepContext and write workflow artifacts. They do
not mutate run/step status; JobWorker and WorkflowRunManager own transitions.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol

from app.config import settings
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowRunStatus, WorkflowStepStatus
from app.services.context_builder import ContextBuilder, StepContext
from app.services.workflow_run_manager import WorkflowRunManager


StepRunner = Callable[[StepContext], Awaitable[Any] | Any]
ChildRunner = Callable[[StepContext, dict[str, Any], int], Awaitable[dict[str, Any]] | dict[str, Any]]


class WorkflowStepExecutor(Protocol):
    async def execute(self, run_id: str, step_name: str) -> StepExecutionResult:
        ...


@dataclass(slots=True)
class StepExecutionResult:
    step_name: str
    artifact_refs: list[dict[str, Any]] = field(default_factory=list)
    child_task_refs: list[dict[str, Any]] = field(default_factory=list)
    skipped_child_tasks: list[str] = field(default_factory=list)


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class UnsupportedWorkflowStepError(ValueError):
    """Raised when no executor is registered for a workflow step."""

    def __init__(self, step_name: str):
        super().__init__(f"Unsupported workflow step: {step_name}")
        self.step_name = step_name
        self.error_code = "WORKFLOW_STEP_UNSUPPORTED"
        self.retryable = False


class WorkflowStepCommitBlocked(RuntimeError):
    """Raised when a runner finishes after workflow pause/cancel was requested."""

    def __init__(self, *, run_id: str, step_name: str, run_status: str):
        super().__init__(
            f"Artifact commit blocked for run {run_id} step {step_name}: run status is {run_status}"
        )
        self.run_id = run_id
        self.step_name = step_name
        self.run_status = run_status
        self.error_code = "WORKFLOW_STEP_COMMIT_BLOCKED"
        self.retryable = False


class StepExecutorRegistry:
    """Map canonical workflow step names to executable step adapters."""

    def __init__(self, executors: Optional[dict[str, WorkflowStepExecutor]] = None) -> None:
        self._executors: dict[str, WorkflowStepExecutor] = dict(executors or {})

    def register(self, step_name: str, executor: WorkflowStepExecutor) -> None:
        self._executors[step_name] = executor

    def get(self, step_name: str) -> WorkflowStepExecutor:
        try:
            return self._executors[step_name]
        except KeyError as exc:
            raise UnsupportedWorkflowStepError(step_name) from exc

    async def execute(self, *, run_id: str, step_name: str) -> StepExecutionResult:
        executor = self.get(step_name)
        return await executor.execute(run_id, step_name)


class ArtifactStepExecutor:
    """Execute a canonical agent runner and persist its payload as one artifact."""

    def __init__(
        self,
        *,
        runner: StepRunner,
        artifact_type: WorkflowArtifactType,
        db_path: Optional[str] = None,
        context_builder: Optional[ContextBuilder] = None,
        summary_text: Optional[str] = None,
    ) -> None:
        self.base = BaseStepExecutor(db_path=db_path, context_builder=context_builder)
        self.runner = runner
        self.artifact_type = artifact_type
        self.summary_text = summary_text or artifact_type.value

    async def execute(self, run_id: str, step_name: str) -> StepExecutionResult:
        context = await self.base.build_context(run_id, step_name)
        payload = await _maybe_await(self.runner(context))
        if not isinstance(payload, dict):
            payload = {"items": payload} if isinstance(payload, list) else {"value": payload}
        ref = await self.base._create_artifact(
            context,
            artifact_type=self.artifact_type,
            payload=payload,
            summary_text=self.summary_text,
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])


class BaseStepExecutor:
    def __init__(self, db_path: Optional[str] = None, context_builder: Optional[ContextBuilder] = None) -> None:
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self.context_builder = context_builder or ContextBuilder(self.db_path)

    async def build_context(self, run_id: str, step_name: str) -> StepContext:
        return await self.context_builder.build_context(run_id, step_name)

    async def _create_artifact(
        self,
        context: StepContext,
        *,
        artifact_type: WorkflowArtifactType,
        payload: dict[str, Any],
        summary_text: Optional[str] = None,
        parent_artifact_id: Optional[str] = None,
        artifact_version: int = 1,
    ) -> dict[str, Any]:
        await self._assert_artifact_commit_allowed(context)
        async with WorkflowRunManager(self.db_path) as manager:
            artifact = await manager.attach_artifact(
                run_id=context.run["run_id"],
                artifact_type=artifact_type,
                parent_artifact_id=parent_artifact_id,
                payload=payload,
                summary_text=summary_text,
                created_by_step_id=context.step["step_id"],
                artifact_version=artifact_version,
            )
        return {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type.value,
            "artifact_version": artifact.artifact_version,
            "parent_artifact_id": artifact.parent_artifact_id,
        }

    async def _assert_artifact_commit_allowed(self, context: StepContext) -> None:
        run_id = str(context.run["run_id"])
        step_name = str(context.step["step_name"])
        async with WorkflowStore(self.db_path) as store:
            run = await store.get_run(run_id)
        status = run.status.value if run is not None else "missing"
        # T10.1: external calls can finish after a user pause/cancel request.
        # A technical success is only valid while the workflow run is still running.
        if status != WorkflowRunStatus.RUNNING.value:
            raise WorkflowStepCommitBlocked(
                run_id=run_id,
                step_name=step_name,
                run_status=status,
            )

    async def _emit_embedding_initializing(self, context: StepContext) -> None:
        async with WorkflowStore(self.db_path) as store:
            await store.append_event(
                run_id=str(context.run["run_id"]),
                thread_id=str(context.run["thread_id"]),
                step_id=str(context.step["step_id"]),
                event_type="embedding_initializing",
                payload={"message": "正在初始化本地向量模型（首次较慢）"},
            )


class PassthroughStepExecutor(BaseStepExecutor):
    """Complete workflow bookkeeping steps that do not create artifacts."""

    async def execute(self, run_id: str, step_name: str) -> StepExecutionResult:
        await self.build_context(run_id, step_name)
        return StepExecutionResult(step_name=step_name)


class DiscoveryStepExecutor(BaseStepExecutor):
    def __init__(self, *, source_runner: StepRunner, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.source_runner = source_runner

    async def execute(self, run_id: str, step_name: str = "discovery.spider_search") -> StepExecutionResult:
        context = await self.build_context(run_id, step_name)
        payload = await _maybe_await(self.source_runner(context))
        ref = await self._create_artifact(
            context,
            artifact_type=WorkflowArtifactType.SOURCE_SNAPSHOT,
            payload={"items": payload if isinstance(payload, list) else payload},
            summary_text="source snapshot",
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])


class RetrievalStepExecutor(BaseStepExecutor):
    def __init__(self, *, rag_runner: StepRunner, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rag_runner = rag_runner

    async def execute(self, run_id: str, step_name: str = "retrieval.rag_retrieve") -> StepExecutionResult:
        context = await self.build_context(run_id, step_name)
        await self._emit_embedding_initializing(context)
        payload = await _maybe_await(self.rag_runner(context))
        artifact_type = (
            WorkflowArtifactType.RAG_INDEX
            if step_name == "retrieval.rag_index"
            else WorkflowArtifactType.RAG_RESULT
        )
        ref = await self._create_artifact(
            context,
            artifact_type=artifact_type,
            payload=payload if isinstance(payload, dict) else {"items": payload},
            summary_text=artifact_type.value,
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])


class StrategyStepExecutor(BaseStepExecutor):
    def __init__(self, *, strategy_runner: StepRunner, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.strategy_runner = strategy_runner

    async def execute(self, run_id: str, step_name: str = "strategy.llm_synthesize") -> StepExecutionResult:
        context = await self.build_context(run_id, step_name)
        payload = await _maybe_await(self.strategy_runner(context))
        ref = await self._create_artifact(
            context,
            artifact_type=WorkflowArtifactType.STRATEGY,
            payload=payload if isinstance(payload, dict) else {"value": payload},
            summary_text="strategy",
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])


class GenerationStepExecutor(BaseStepExecutor):
    def __init__(
        self,
        *,
        proposal_runner: Optional[StepRunner] = None,
        note_runner: Optional[ChildRunner] = None,
        similarity_runner: Optional[StepRunner] = None,
        rewrite_runner: Optional[StepRunner] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.proposal_runner = proposal_runner
        self.note_runner = note_runner
        self.similarity_runner = similarity_runner
        self.rewrite_runner = rewrite_runner

    async def execute(self, run_id: str, step_name: str) -> StepExecutionResult:
        if step_name == "generation.plan_proposals":
            return await self._plan_proposals(run_id, step_name)
        if step_name == "generation.select_proposals":
            return await self._select_proposals(run_id, step_name)
        if step_name == "generation.generate_notes_parallel":
            return await self._generate_notes_parallel(run_id, step_name)
        if step_name == "generation.similarity_check":
            return await self._similarity_check(run_id, step_name)
        if step_name == "generation.rewrite_or_reselect":
            return await self._rewrite_or_reselect(run_id, step_name)
        if step_name == "generation.aggregate_notes":
            return await self._aggregate_notes(run_id, step_name)
        raise ValueError(f"Unsupported generation step: {step_name}")

    async def _plan_proposals(self, run_id: str, step_name: str) -> StepExecutionResult:
        if self.proposal_runner is None:
            raise ValueError("proposal_runner is required")
        context = await self.build_context(run_id, step_name)
        proposals = await _maybe_await(self.proposal_runner(context))
        refs: list[dict[str, Any]] = []
        for proposal in proposals:
            refs.append(
                await self._create_artifact(
                    context,
                    artifact_type=WorkflowArtifactType.PROPOSAL,
                    payload=proposal,
                    summary_text=str(proposal.get("title") or proposal.get("proposal_id") or "proposal"),
                )
            )
        return StepExecutionResult(step_name=step_name, artifact_refs=refs)

    async def _select_proposals(self, run_id: str, step_name: str) -> StepExecutionResult:
        if self.proposal_runner is None:
            raise ValueError("proposal_runner is required")
        context = await self.build_context(run_id, step_name)
        proposals = await _maybe_await(self.proposal_runner(context))
        refs: list[dict[str, Any]] = []
        for proposal in proposals:
            refs.append(
                await self._create_artifact(
                    context,
                    artifact_type=WorkflowArtifactType.PROPOSAL,
                    payload=proposal,
                    summary_text=str(proposal.get("title") or proposal.get("proposal_id") or "selected proposal"),
                )
            )
        return StepExecutionResult(step_name=step_name, artifact_refs=refs)

    async def _generate_notes_parallel(self, run_id: str, step_name: str) -> StepExecutionResult:
        if self.note_runner is None:
            raise ValueError("note_runner is required")
        context = await self.build_context(run_id, step_name)
        targets = context.generation_targets or context.input_artifacts
        async with WorkflowStore(self.db_path) as store:
            existing = await store.list_child_tasks(run_id)
            by_slot = {task.slot_index: task for task in existing if task.step_id == context.step["step_id"]}
        missing_tasks = [
            {
                "task_type": "note_generation",
                "slot_index": index,
                "proposal_id": target.get("artifact_id"),
            }
            for index, target in enumerate(targets)
            if index not in by_slot
        ]
        if missing_tasks:
            async with WorkflowRunManager(self.db_path) as manager:
                created = await manager.create_child_tasks(
                    run_id=run_id,
                    step_id=context.step["step_id"],
                    tasks=missing_tasks,
                )
            by_slot.update({task.slot_index: task for task in created})
        child_tasks = [by_slot[index] for index, _target in enumerate(targets)]

        refs: list[dict[str, Any]] = []
        skipped: list[str] = []
        for index, task in enumerate(child_tasks):
            if task.status == WorkflowStepStatus.SUCCEEDED and task.output_artifact_refs_json:
                refs.extend(task.output_artifact_refs_json)
                skipped.append(task.child_task_id)
                continue
            target = targets[index]
            async with WorkflowRunManager(self.db_path) as manager:
                await manager.start_child_task(task.child_task_id)
            try:
                note_payload = await _maybe_await(self.note_runner(context, target, index))
            except Exception as exc:  # noqa: BLE001
                async with WorkflowRunManager(self.db_path) as manager:
                    await manager.retry_child_task(
                        task.child_task_id,
                        {
                            "code": getattr(exc, "error_code", "NOTE_GENERATION_ERROR"),
                            "message": str(exc),
                        },
                    )
                continue
            if isinstance(note_payload, dict):
                generation_params = note_payload.setdefault("generation_params", {})
                target_payload = target.get("payload_json") if isinstance(target, dict) else {}
                if isinstance(generation_params, dict) and isinstance(target_payload, dict):
                    generation_params.setdefault("proposal_id", target_payload.get("proposal_id") or target.get("artifact_id"))
                    generation_params.setdefault("proposal_score", target_payload.get("score"))
                    generation_params.setdefault("proposal_angle", target_payload.get("angle"))
            ref = await self._create_artifact(
                context,
                artifact_type=WorkflowArtifactType.GENERATED_NOTE,
                payload=note_payload,
                summary_text=str(note_payload.get("title") or "generated note"),
            )
            async with WorkflowRunManager(self.db_path) as manager:
                await manager.complete_child_task(
                    task.child_task_id,
                    artifact_refs=[ref],
                    note_id=ref["artifact_id"],
                )
            refs.append(ref)
        return StepExecutionResult(step_name=step_name, artifact_refs=refs, skipped_child_tasks=skipped)

    async def _similarity_check(self, run_id: str, step_name: str) -> StepExecutionResult:
        if self.similarity_runner is None:
            raise ValueError("similarity_runner is required")
        context = await self.build_context(run_id, step_name)
        await self._emit_embedding_initializing(context)
        payload = await _maybe_await(self.similarity_runner(context))
        ref = await self._create_artifact(
            context,
            artifact_type=WorkflowArtifactType.SIMILARITY_REPORT,
            payload=payload if isinstance(payload, dict) else {"value": payload},
            summary_text="similarity report",
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])

    async def _rewrite_or_reselect(self, run_id: str, step_name: str) -> StepExecutionResult:
        if self.rewrite_runner is None:
            raise ValueError("rewrite_runner is required")
        context = await self.build_context(run_id, step_name)
        if not context.revision_targets:
            raise ValueError("rewrite step requires a target generated note artifact")
        parent = context.revision_targets[0]
        payload = await _maybe_await(self.rewrite_runner(context))
        ref = await self._create_artifact(
            context,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            artifact_version=int(parent.get("artifact_version") or 1) + 1,
            parent_artifact_id=parent["artifact_id"],
            payload=payload if isinstance(payload, dict) else {"value": payload},
            summary_text=str(payload.get("title") if isinstance(payload, dict) else "rewrite"),
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])

    async def _aggregate_notes(self, run_id: str, step_name: str) -> StepExecutionResult:
        context = await self.build_context(run_id, step_name)
        payload = {
            "generated_notes": [
                artifact
                for artifact in context.input_artifacts
                if artifact.get("artifact_type") == WorkflowArtifactType.GENERATED_NOTE.value
            ]
        }
        ref = await self._create_artifact(
            context,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload=payload,
            summary_text="aggregated generated notes",
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])

class FinalizationStepExecutor(BaseStepExecutor):
    async def execute(self, run_id: str, step_name: str = "finalization.persist_artifacts") -> StepExecutionResult:
        context = await self.build_context(run_id, step_name)
        payload = {
            "artifact_refs": [
                {"artifact_id": artifact["artifact_id"], "artifact_type": artifact["artifact_type"]}
                for artifact in context.input_artifacts
            ]
        }
        ref = await self._create_artifact(
            context,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload=payload,
            summary_text="final result",
        )
        return StepExecutionResult(step_name=step_name, artifact_refs=[ref])


def build_agent_step_executor_registry(
    *,
    db_path: Optional[str] = None,
    strategy_agent: Any = None,
    generation_agent: Any = None,
) -> StepExecutorRegistry:
    """Build the production registry that binds canonical steps to agent runners."""

    if strategy_agent is None:
        from app.agents.content_strategy_agent import ContentStrategyAgent

        strategy_agent = ContentStrategyAgent()
    if generation_agent is None:
        from app.agents.content_generation_agent import ContentGenerationAgent

        generation_agent = ContentGenerationAgent()

    registry = StepExecutorRegistry()
    passthrough_executor = PassthroughStepExecutor(db_path=db_path)
    for step_name in (
        "intake.capture_request",
        "context.build_context",
        "context.load_constraints",
        "context.load_previous_artifacts",
        "finalization.emit_result_ready",
        "review.await_user_acceptance",
        "review.publish_candidates",
    ):
        registry.register(step_name, passthrough_executor)

    registry.register(
        "discovery.plan_queries",
        ArtifactStepExecutor(
            db_path=db_path,
            runner=strategy_agent.plan_queries_step,
            artifact_type=WorkflowArtifactType.SOURCE_SNAPSHOT,
            summary_text="planned queries",
        ),
    )
    registry.register(
        "discovery.spider_search",
        DiscoveryStepExecutor(db_path=db_path, source_runner=strategy_agent.spider_search_step),
    )
    registry.register(
        "discovery.assess_source_quality",
        ArtifactStepExecutor(
            db_path=db_path,
            runner=strategy_agent.assess_source_quality_step,
            artifact_type=WorkflowArtifactType.SOURCE_SNAPSHOT,
            summary_text="source quality assessment",
        ),
    )
    registry.register(
        "discovery.expand_queries",
        ArtifactStepExecutor(
            db_path=db_path,
            runner=strategy_agent.expand_queries_step,
            artifact_type=WorkflowArtifactType.SOURCE_SNAPSHOT,
            summary_text="expanded queries",
        ),
    )
    registry.register(
        "discovery.persist_sources",
        DiscoveryStepExecutor(db_path=db_path, source_runner=strategy_agent.persist_sources_step),
    )
    registry.register(
        "retrieval.rag_index",
        RetrievalStepExecutor(db_path=db_path, rag_runner=strategy_agent.rag_index_step),
    )
    registry.register(
        "retrieval.rag_retrieve",
        RetrievalStepExecutor(db_path=db_path, rag_runner=strategy_agent.rag_retrieve_step),
    )
    registry.register(
        "strategy.prepare_prompt",
        ArtifactStepExecutor(
            db_path=db_path,
            runner=strategy_agent.prepare_prompt_step,
            artifact_type=WorkflowArtifactType.STRATEGY,
            summary_text="strategy prompt",
        ),
    )
    registry.register(
        "strategy.llm_synthesize",
        StrategyStepExecutor(db_path=db_path, strategy_runner=strategy_agent.llm_synthesize_step),
    )
    registry.register(
        "strategy.validate_strategy",
        StrategyStepExecutor(db_path=db_path, strategy_runner=strategy_agent.validate_strategy_step),
    )
    registry.register(
        "strategy.persist_strategy",
        StrategyStepExecutor(db_path=db_path, strategy_runner=strategy_agent.persist_strategy_step),
    )

    generation_executor = GenerationStepExecutor(
        db_path=db_path,
        proposal_runner=generation_agent.plan_proposals_step,
        note_runner=generation_agent.generate_note_child_step,
        similarity_runner=generation_agent.similarity_check_step,
        rewrite_runner=generation_agent.rewrite_or_reselect_step,
    )
    registry.register("generation.plan_proposals", generation_executor)
    registry.register(
        "generation.select_proposals",
        GenerationStepExecutor(db_path=db_path, proposal_runner=generation_agent.select_proposals_step),
    )
    registry.register("generation.generate_notes_parallel", generation_executor)
    registry.register("generation.similarity_check", generation_executor)
    registry.register("generation.rewrite_or_reselect", generation_executor)
    registry.register("generation.aggregate_notes", generation_executor)
    finalization_executor = FinalizationStepExecutor(db_path=db_path)
    registry.register("finalization.persist_artifacts", finalization_executor)
    return registry
