"""Background worker for consuming jobs from the persistent SQLite queue."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.logging_config import get_logger, log_event
from app.memory.job_store import JobRecord, JobStore
from app.memory.session_state import SessionManager
from app.models.session import SessionError, SessionStage


class JobExecutionError(RuntimeError):
    """Base exception for job execution failures."""

    def __init__(self, message: str, *, error_code: str = "JOB_EXECUTION_ERROR", retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class RetryableJobError(JobExecutionError):
    def __init__(self, message: str, *, error_code: str = "JOB_RETRYABLE_ERROR"):
        super().__init__(message, error_code=error_code, retryable=True)


class PermanentJobError(JobExecutionError):
    def __init__(self, message: str, *, error_code: str = "JOB_PERMANENT_ERROR"):
        super().__init__(message, error_code=error_code, retryable=False)


@dataclass(slots=True)
class JobExecutionResult:
    success: bool
    retry_scheduled: bool = False
    failed: bool = False


class JobWorker:
    """Poll jobs table and execute strategy/generation jobs."""

    def __init__(
        self,
        *,
        job_store: JobStore,
        orchestrator,
        poll_interval_ms: Optional[int] = None,
    ):
        self.job_store = job_store
        self.orchestrator = orchestrator
        self.poll_interval_ms = poll_interval_ms if poll_interval_ms is not None else settings.JOB_POLL_INTERVAL_MS
        self._logger = get_logger(__name__, component="worker")

    async def _mark_session_failed(self, *, session_id: str, job_type: str, error_code: str, message: str) -> None:
        stage_map = {
            "strategy": SessionStage.STRATEGY,
            "generate": SessionStage.GENERATION,
        }
        async with SessionManager(self.job_store.db_path) as manager:
            session = await manager.get_session(session_id)
            if session is None:
                return
            await manager.update_session(
                session_id,
                stage=SessionStage.FAILED,
                error=SessionError(
                    code=error_code,
                    message=message,
                    stage=stage_map.get(job_type, SessionStage.FAILED),
                ),
            )

    async def run_loop(self, *, stop_event: asyncio.Event) -> None:
        """Run polling loop until stop_event is set."""
        if settings.JOB_RECOVERY_ON_STARTUP:
            log_event(
                self._logger,
                event_name="recovery_started",
                component="worker",
                level="info",
            )
            recovered = await self.job_store.recover_expired_running_jobs()
            log_event(
                self._logger,
                event_name="recovery_completed",
                component="worker",
                level="info",
                recovered_jobs=recovered,
            )

        sleep_seconds = self.poll_interval_ms / 1000.0
        while not stop_event.is_set():
            processed = await self.run_once()
            if not processed:
                await asyncio.sleep(sleep_seconds)

    async def run_once(self) -> bool:
        """Process at most one leased job. Returns True if a job was processed."""
        job = await self.job_store.lease_one()
        if job is None:
            return False
        await self._execute_job(job)
        return True

    async def _execute_job(self, job: JobRecord) -> JobExecutionResult:
        await self.job_store.append_session_event(
            session_id=job.session_id,
            job_id=job.id,
            event_name="task_progress",
            stage=job.job_type,
            payload={
                "message": f"{job.job_type} job running",
                "progress": 50,
                "error_code": None,
                "details": {"job_status": "running"},
            },
        )
        try:
            result = await self.orchestrator.run_job(job)
            await self.job_store.mark_succeeded(job.id)
            await self.job_store.append_session_event(
                session_id=job.session_id,
                job_id=job.id,
                event_name="task_completed",
                stage=job.job_type,
                payload={
                    "message": f"{job.job_type} job completed",
                    "progress": 100,
                    "error_code": None,
                    "details": result if isinstance(result, dict) else {},
                },
            )
            return JobExecutionResult(success=True)
        except Exception as exc:  # noqa: BLE001
            error_code = getattr(exc, "error_code", "JOB_EXECUTION_ERROR")
            retryable = bool(getattr(exc, "retryable", False))

            if retryable and job.attempts < job.max_attempts:
                await self.job_store.schedule_retry(
                    job.id,
                    error_code=error_code,
                    error_message=str(exc),
                )
                await self.job_store.append_session_event(
                    session_id=job.session_id,
                    job_id=job.id,
                    event_name="task_failed",
                    stage=job.job_type,
                    payload={
                        "message": str(exc),
                        "progress": None,
                        "error_code": error_code,
                        "details": {"retry_scheduled": True},
                    },
                )
                return JobExecutionResult(success=False, retry_scheduled=True)

            final_error_code = error_code
            if retryable:
                final_error_code = "JOB_MAX_RETRIES_EXCEEDED"

            await self._mark_session_failed(
                session_id=job.session_id,
                job_type=job.job_type,
                error_code=final_error_code,
                message=str(exc),
            )
            await self.job_store.mark_failed(
                job.id,
                error_code=final_error_code,
                error_message=str(exc),
            )
            await self.job_store.append_session_event(
                session_id=job.session_id,
                job_id=job.id,
                event_name="task_failed",
                stage=job.job_type,
                payload={
                    "message": str(exc),
                    "progress": None,
                    "error_code": final_error_code,
                    "details": {"retry_scheduled": False},
                },
            )
            return JobExecutionResult(success=False, failed=True)
