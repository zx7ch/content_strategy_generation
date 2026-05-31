"""Task orchestrator for strategy/generation job execution."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

ProgressCallback = Callable[[str], Awaitable[None]]

from app.agents.content_generation_agent import ContentGenerationAgent
from app.agents.content_generation_agent import GenerationExecutionResult
from app.agents.content_strategy_agent import ContentStrategyAgent
from app.config import settings
from app.memory.job_store import JobRecord
from app.memory.session_state import SessionManager
from app.models.session import SessionLifecycleState
from app.services.step_executors import StepExecutorRegistry, UnsupportedWorkflowStepError


class JobOrchestrationError(RuntimeError):
    """Raised when a job cannot complete in orchestrator."""

    def __init__(self, message: str, *, error_code: str, retryable: bool):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


Runner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class Orchestrator:
    """Dispatch job execution to the corresponding agent pipeline."""

    RETRYABLE_CODES = {
        "SPIDER_SERVICE_UNAVAILABLE",
        "SPIDER_RATE_LIMITED",
        "LLM_TIMEOUT",
        "LLM_RATE_LIMITED",
    }

    def __init__(
        self,
        *,
        db_path: Optional[str] = None,
        strategy_runner: Optional[Runner] = None,
        generation_runner: Optional[Runner] = None,
        step_executor_registry: Optional[StepExecutorRegistry] = None,
    ):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._strategy_runner = strategy_runner or self._run_strategy_job
        self._generation_runner = generation_runner or self._run_generation_job
        self._step_executor_registry = step_executor_registry or StepExecutorRegistry()

    async def run_job(self, job: JobRecord, *, progress_callback: Optional[ProgressCallback] = None) -> dict[str, Any]:
        """Validate session state then execute strategy/generation job."""
        if job.run_id:
            # WorkflowRun jobs are the new execution truth and intentionally do
            # not require a legacy session row.
            return await self.run_workflow_step(job)

        async with SessionManager(self.db_path) as session_manager:
            session = await session_manager.get_session(job.session_id)
            if session is None:
                raise JobOrchestrationError(
                    "Session not found",
                    error_code="SESSION_NOT_FOUND",
                    retryable=False,
                )
            if session.lifecycle_state == SessionLifecycleState.PURGED:
                raise JobOrchestrationError(
                    "Session has been purged",
                    error_code="SESSION_PURGED",
                    retryable=False,
                )
            if session.lifecycle_state == SessionLifecycleState.FROZEN:
                raise JobOrchestrationError(
                    "Session is frozen",
                    error_code="SESSION_FROZEN",
                    retryable=False,
                )

        if job.job_type == "strategy":
            return await self._strategy_runner(job.session_id, job.payload, progress_callback=progress_callback)
        if job.job_type == "generate":
            return await self._generation_runner(job.session_id, job.payload, progress_callback=progress_callback)

        raise JobOrchestrationError(
            f"Unsupported job_type: {job.job_type}",
            error_code="JOB_TYPE_UNSUPPORTED",
            retryable=False,
        )

    async def run_workflow_step(self, job: JobRecord) -> dict[str, Any]:
        """Execute a workflow-bound job through the step executor registry."""
        if not job.run_id:
            raise JobOrchestrationError(
                "Workflow job missing run_id",
                error_code="WORKFLOW_RUN_ID_MISSING",
                retryable=False,
            )
        step_name = job.payload.get("step_name")
        if not step_name:
            raise JobOrchestrationError(
                "Workflow job missing step_name",
                error_code="WORKFLOW_STEP_NAME_MISSING",
                retryable=False,
            )
        try:
            result = await self._step_executor_registry.execute(
                run_id=job.run_id,
                step_name=str(step_name),
            )
        except UnsupportedWorkflowStepError as exc:
            raise JobOrchestrationError(
                str(exc),
                error_code=exc.error_code,
                retryable=exc.retryable,
            ) from exc

        return {
            "success": True,
            "step_name": result.step_name,
            "artifact_refs": result.artifact_refs,
            "child_task_refs": result.child_task_refs,
            "skipped_child_tasks": result.skipped_child_tasks,
        }

    async def _run_strategy_job(self, session_id: str, payload: dict[str, Any], *, progress_callback: Optional[ProgressCallback] = None) -> dict[str, Any]:
        del payload  # reserved for future strategy options
        agent = ContentStrategyAgent(session_manager=SessionManager(self.db_path))
        result = await agent.execute(session_id, progress_callback=progress_callback)
        if not result.success:
            retryable = result.error_code in self.RETRYABLE_CODES
            raise JobOrchestrationError(
                result.message or "Strategy execution failed",
                error_code=result.error_code or "STRATEGY_EXECUTION_ERROR",
                retryable=retryable,
            )
        return {
            "success": True,
            "quality_score": result.quality_score,
            "used_fallback": result.used_fallback,
        }

    async def _run_generation_job(self, session_id: str, payload: dict[str, Any], *, progress_callback: Optional[ProgressCallback] = None) -> dict[str, Any]:
        del payload  # session-backed generation uses stored strategy/session data
        agent = ContentGenerationAgent(session_manager=SessionManager(self.db_path))
        generated: GenerationExecutionResult = await agent.execute(session_id, progress_callback=progress_callback)
        if not generated.success:
            retryable = generated.error_code in self.RETRYABLE_CODES
            raise JobOrchestrationError(
                generated.message or "Generation execution failed",
                error_code=generated.error_code or "GENERATION_EXECUTION_ERROR",
                retryable=retryable,
            )
        return {
            "success": True,
            "status": generated.status,
            "notes_generated": len(generated.notes),
            "similarity_report": generated.similarity_report,
        }
