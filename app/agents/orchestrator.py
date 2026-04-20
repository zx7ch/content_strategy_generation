"""Task orchestrator for strategy/generation job execution."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from app.agents.content_generation_agent import ContentGenerationAgent
from app.agents.content_generation_agent import GenerationExecutionResult
from app.agents.content_strategy_agent import ContentStrategyAgent
from app.config import settings
from app.memory.job_store import JobRecord
from app.memory.session_state import SessionManager
from app.models.session import SessionLifecycleState


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
    ):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._strategy_runner = strategy_runner or self._run_strategy_job
        self._generation_runner = generation_runner or self._run_generation_job

    async def run_job(self, job: JobRecord) -> dict[str, Any]:
        """Validate session state then execute strategy/generation job."""
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
            return await self._strategy_runner(job.session_id, job.payload)
        if job.job_type == "generate":
            return await self._generation_runner(job.session_id, job.payload)

        raise JobOrchestrationError(
            f"Unsupported job_type: {job.job_type}",
            error_code="JOB_TYPE_UNSUPPORTED",
            retryable=False,
        )

    async def _run_strategy_job(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del payload  # reserved for future strategy options
        agent = ContentStrategyAgent(session_manager=SessionManager(self.db_path))
        result = await agent.execute(session_id)
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

    async def _run_generation_job(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del payload  # session-backed generation uses stored strategy/session data
        agent = ContentGenerationAgent(session_manager=SessionManager(self.db_path))
        generated: GenerationExecutionResult = await agent.execute(session_id)
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
