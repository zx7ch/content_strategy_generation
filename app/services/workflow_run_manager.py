"""Workflow state-transition manager.

This module is the single write boundary for workflow run/step state. External
callers should report commands or transitions here instead of mutating workflow
tables directly.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable, Optional, TypeVar

import aiosqlite

from app.config import settings
from app.memory.workflow_store import WorkflowStore, _json_dump, _new_id
from app.models.workflow import (
    WorkflowArtifact,
    WorkflowArtifactPayloadMode,
    WorkflowArtifactType,
    WorkflowChildTask,
    WorkflowConstraint,
    WorkflowConstraintType,
    WorkflowEvent,
    WorkflowPhase,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.services.workflow_artifact_policy import WorkflowArtifactVersionPolicy


class WorkflowTransitionError(ValueError):
    """Raised when a requested workflow state transition is not allowed."""


T = TypeVar("T")


class WorkflowRunManager:
    """State machine and transactional event writer for workflow runs."""

    TERMINAL_RUN_STATUSES = {
        WorkflowRunStatus.CANCELLED.value,
        WorkflowRunStatus.SUCCEEDED.value,
        WorkflowRunStatus.FAILED.value,
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None

    async def __aenter__(self) -> "WorkflowRunManager":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._conn is not None:
            return
        async with WorkflowStore(self.db_path):
            pass
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _transaction(self, fn: Callable[[], Awaitable[T]]) -> T:
        assert self._conn is not None
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = await fn()
        except Exception:
            await self._conn.rollback()
            raise
        await self._conn.commit()
        return result

    async def _fetch_run_row(self, run_id: str) -> aiosqlite.Row:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowTransitionError(f"Workflow run not found: {run_id}")
        return row

    async def _fetch_step_row(self, run_id: str, step_name: str) -> aiosqlite.Row:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM workflow_steps
            WHERE run_id = ? AND step_name = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, step_name),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowTransitionError(f"Workflow step not found: {step_name}")
        return row

    async def _fetch_step_row_by_id(self, step_id: str) -> aiosqlite.Row:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM workflow_steps WHERE step_id = ?", (step_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowTransitionError(f"Workflow step not found: {step_id}")
        return row

    async def _fetch_child_task_row(self, child_task_id: str) -> aiosqlite.Row:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM workflow_child_tasks WHERE child_task_id = ?",
            (child_task_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowTransitionError(f"Workflow child task not found: {child_task_id}")
        return row

    async def _append_event(
        self,
        *,
        run_id: str,
        thread_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        event_level: str = "info",
        step_id: Optional[str] = None,
        child_task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            INSERT INTO workflow_events (
                run_id, thread_id, step_id, child_task_id, job_id,
                event_type, event_level, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                thread_id,
                step_id,
                child_task_id,
                job_id,
                event_type,
                event_level,
                _json_dump(payload),
            ),
        )

    async def _table_exists(self, table_name: str) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def _set_thread_active_run_if_present(self, thread_id: str, run_id: str) -> None:
        assert self._conn is not None
        if not await self._table_exists("creator_threads"):
            return
        await self._conn.execute(
            "UPDATE creator_threads SET active_run_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (run_id, thread_id),
        )

    async def _pause_workflow_jobs_if_present(self, run_id: str) -> int:
        assert self._conn is not None
        if not await self._table_exists("jobs"):
            return 0
        cursor = await self._conn.execute(
            """
            UPDATE jobs
            SET status='paused', updated_at=CURRENT_TIMESTAMP
            WHERE run_id=? AND status IN ('queued', 'retrying')
            """,
            (run_id,),
        )
        return cursor.rowcount

    async def _resume_workflow_jobs_if_present(self, run_id: str) -> int:
        assert self._conn is not None
        if not await self._table_exists("jobs"):
            return 0
        cursor = await self._conn.execute(
            """
            UPDATE jobs
            SET status='queued', not_before=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE run_id=? AND status='paused'
            """,
            (run_id,),
        )
        return cursor.rowcount

    async def _cancel_workflow_jobs_if_present(self, run_id: str, reason: str) -> int:
        assert self._conn is not None
        if not await self._table_exists("jobs"):
            return 0
        cursor = await self._conn.execute(
            """
            UPDATE jobs
            SET status='cancelled', cancel_reason=?, lease_expires_at=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE run_id=? AND status IN ('queued', 'paused', 'retrying', 'running')
            """,
            (reason, run_id),
        )
        return cursor.rowcount

    async def _pause_job_if_present(self, job_id: Optional[str]) -> None:
        assert self._conn is not None
        if not job_id or not await self._table_exists("jobs"):
            return
        await self._conn.execute(
            """
            UPDATE jobs
            SET status='paused', lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='running'
            """,
            (job_id,),
        )

    @staticmethod
    def _normalize_error(error: str | dict[str, Any]) -> tuple[str, str]:
        if isinstance(error, dict):
            code = str(error.get("code") or "WORKFLOW_ERROR")
            message = str(error.get("message") or error)
            return code, message
        return "WORKFLOW_ERROR", error

    @staticmethod
    def _run(row: aiosqlite.Row) -> WorkflowRun:
        return WorkflowStore._row_to_run(row)

    @staticmethod
    def _step(row: aiosqlite.Row) -> WorkflowStep:
        return WorkflowStore._row_to_step(row)

    @staticmethod
    def _child_task(row: aiosqlite.Row) -> WorkflowChildTask:
        return WorkflowStore._row_to_child_task(row)

    @staticmethod
    def _artifact(row: aiosqlite.Row) -> WorkflowArtifact:
        return WorkflowStore._row_to_artifact(row)

    @staticmethod
    def _constraint(row: aiosqlite.Row) -> WorkflowConstraint:
        return WorkflowStore._row_to_constraint(row)

    @staticmethod
    def _event(row: aiosqlite.Row) -> WorkflowEvent:
        return WorkflowStore._row_to_event(row)

    async def start_run(
        self,
        *,
        thread_id: str,
        user_id: str,
        user_message_id: Optional[str] = None,
        initial_request: Optional[str] = None,
    ) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            run_id = _new_id("run")
            await self._conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, thread_id, user_id, status, phase, interrupt_policy,
                    source_message_id, started_at, created_at, updated_at
                )
                VALUES (?, ?, ?, 'running', 'intake', 'safe_boundary', ?,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (run_id, thread_id, user_id, user_message_id),
            )
            await self._set_thread_active_run_if_present(thread_id, run_id)
            await self._append_event(
                run_id=run_id,
                thread_id=thread_id,
                event_type="run_started",
                payload={"user_message_id": user_message_id, "initial_request": initial_request},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def pause_run(self, run_id: str, reason: str = "user_pause") -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            row = await self._fetch_run_row(run_id)
            status = row["status"]
            if status in {"pausing", "paused"}:
                return self._run(row)
            self._ensure_not_terminal(status, "pause_run")
            if status != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(f"pause_run not allowed from {status}")
            paused_jobs = await self._pause_workflow_jobs_if_present(run_id)
            await self._conn.execute(
                "UPDATE workflow_runs SET status='pausing', updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=row["thread_id"],
                event_type="run_pause_requested",
                payload={"reason": reason, "paused_job_count": paused_jobs},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def resume_run(self, run_id: str) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            row = await self._fetch_run_row(run_id)
            status = row["status"]
            self._ensure_not_terminal(status, "resume_run")
            if status not in {"paused", "waiting_user"}:
                raise WorkflowTransitionError(f"resume_run not allowed from {status}")
            resumed_jobs = await self._resume_workflow_jobs_if_present(run_id)
            await self._conn.execute(
                "UPDATE workflow_runs SET status='running', updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=row["thread_id"],
                event_type="run_resumed",
                payload={"resumed_job_count": resumed_jobs},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def cancel_run(self, run_id: str, reason: str = "user_cancelled") -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            row = await self._fetch_run_row(run_id)
            status = row["status"]
            if status in {"cancelling", "cancelled"}:
                return self._run(row)
            if status in {WorkflowRunStatus.SUCCEEDED.value, WorkflowRunStatus.FAILED.value}:
                raise WorkflowTransitionError(f"cancel_run not allowed from {status}")
            if status not in {"running", "pausing", "paused"}:
                raise WorkflowTransitionError(f"cancel_run not allowed from {status}")
            cancelled_jobs = await self._cancel_workflow_jobs_if_present(run_id, reason)
            await self._conn.execute(
                "UPDATE workflow_runs SET status='cancelling', updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (run_id,),
            )
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='cancelled', completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status IN ('pending', 'retrying')
                """,
                (run_id,),
            )
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='cancelled', completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status IN ('pending', 'running', 'retrying')
                """,
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=row["thread_id"],
                event_type="run_cancel_requested",
                payload={"reason": reason, "cancelled_job_count": cancelled_jobs},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def ack_pause_at_boundary(
        self,
        run_id: str,
        step_name: str,
        *,
        job_id: Optional[str] = None,
    ) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] == WorkflowRunStatus.PAUSED.value:
                return self._run(run)
            if run["status"] != WorkflowRunStatus.PAUSING.value:
                raise WorkflowTransitionError(f"ack_pause_at_boundary not allowed from {run['status']}")
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] == WorkflowStepStatus.RUNNING.value:
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='retrying', attempt_count=attempt_count + 1,
                        active_job_id=NULL, next_retry_at=CURRENT_TIMESTAMP,
                        error_code='RUN_PAUSED', error_message='run paused at safe boundary',
                        updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=?
                    """,
                    (step["step_id"],),
                )
            await self._pause_job_if_present(job_id or run["active_job_id"])
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET status='paused', active_job_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                job_id=job_id,
                event_type="run_paused",
                payload={"step_name": step_name, "reason": "safe_boundary_ack"},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def ack_cancel_at_boundary(
        self,
        run_id: str,
        step_name: str,
        *,
        job_id: Optional[str] = None,
    ) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] == WorkflowRunStatus.CANCELLED.value:
                return self._run(run)
            if run["status"] != WorkflowRunStatus.CANCELLING.value:
                raise WorkflowTransitionError(f"ack_cancel_at_boundary not allowed from {run['status']}")
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] in {"pending", "running", "retrying"}:
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='cancelled', active_job_id=NULL,
                        completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=?
                    """,
                    (step["step_id"],),
                )
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET status='cancelled', cancelled_at=COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                    active_job_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                job_id=job_id,
                event_type="run_cancelled",
                payload={"step_name": step_name, "reason": "safe_boundary_ack"},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def complete_run(self, run_id: str) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            row = await self._fetch_run_row(run_id)
            status = row["status"]
            self._ensure_not_terminal(status, "complete_run")
            if status != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(f"complete_run not allowed from {status}")
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET status='succeeded', completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=row["thread_id"],
                event_type="run_succeeded",
                payload={},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def fail_run(self, run_id: str, error: str | dict[str, Any]) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            row = await self._fetch_run_row(run_id)
            status = row["status"]
            self._ensure_not_terminal(status, "fail_run")
            if status not in {"running", "pausing"}:
                raise WorkflowTransitionError(f"fail_run not allowed from {status}")
            code, message = self._normalize_error(error)
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET status='failed', failed_at=CURRENT_TIMESTAMP, error_code=?,
                    error_message=?, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (code, message, run_id),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=row["thread_id"],
                event_type="run_failed",
                event_level="error",
                payload={"error_code": code, "error_message": message},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def initialize_steps(
        self, run_id: str, workflow_template: list[dict[str, Any]]
    ) -> list[WorkflowStep]:
        async def op() -> list[WorkflowStep]:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            self._ensure_not_terminal(run["status"], "initialize_steps")
            steps: list[WorkflowStep] = []
            for item in workflow_template:
                step_id = _new_id("step")
                step_name = str(item["step_name"])
                phase = item.get("phase", WorkflowPhase.INTAKE.value)
                phase_value = phase.value if isinstance(phase, WorkflowPhase) else str(phase)
                max_attempts = int(item.get("max_attempts", 3))
                input_hash = item.get("input_hash")
                checkpoint = item.get("checkpoint")
                await self._conn.execute(
                    """
                    INSERT INTO workflow_steps (
                        step_id, run_id, step_name, phase, status, max_attempts,
                        input_hash, checkpoint_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        step_id,
                        run_id,
                        step_name,
                        phase_value,
                        max_attempts,
                        input_hash,
                        _json_dump(checkpoint) if checkpoint is not None else None,
                    ),
                )
                steps.append(self._step(await self._fetch_step_row(run_id, step_name)))
            if steps:
                await self._conn.execute(
                    """
                    UPDATE workflow_runs
                    SET current_step=COALESCE(current_step, ?), phase=COALESCE(?, phase),
                        updated_at=CURRENT_TIMESTAMP
                    WHERE run_id=?
                    """,
                    (steps[0].step_name, steps[0].phase.value, run_id),
                )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                event_type="steps_initialized",
                payload={"step_count": len(steps), "steps": [step.step_name for step in steps]},
            )
            return steps

        return await self._transaction(op)

    async def start_step(
        self, run_id: str, step_name: str, job_id: Optional[str] = None
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(f"start_step requires running run, got {run['status']}")
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] not in {"pending", "retrying"}:
                raise WorkflowTransitionError(f"start_step not allowed from {step['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='running', active_job_id=?, started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (job_id, step["step_id"]),
            )
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET current_step=?, phase=?, active_job_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (step_name, step["phase"], job_id, run_id),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                job_id=job_id,
                event_type="step_started",
                payload={"step_name": step_name},
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def complete_step(
        self,
        run_id: str,
        step_name: str,
        artifact_refs: Optional[list[dict[str, Any]]] = None,
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            if run["status"] in {"cancelling", "cancelled"}:
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='cancelled', completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=? AND status='running'
                    """,
                    (step["step_id"],),
                )
                await self._conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status='cancelled', cancelled_at=COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                        active_job_id=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE run_id=?
                    """,
                    (run_id,),
                )
                await self._append_event(
                    run_id=run_id,
                    thread_id=run["thread_id"],
                    step_id=step["step_id"],
                    event_type="step_cancelled",
                    payload={"step_name": step_name, "reason": "commit_guard"},
                )
                return self._step(await self._fetch_step_row(run_id, step_name))
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(f"complete_step requires running run, got {run['status']}")
            if step["status"] != "running":
                raise WorkflowTransitionError(f"complete_step not allowed from {step['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='succeeded', output_artifact_refs_json=?, active_job_id=NULL,
                    completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (json.dumps(artifact_refs or [], ensure_ascii=False, default=str), step["step_id"]),
            )
            await self._conn.execute(
                "UPDATE workflow_runs SET active_job_id=NULL, updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                event_type="step_completed",
                payload={"step_name": step_name, "artifact_refs": artifact_refs or []},
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def retry_step(
        self,
        run_id: str,
        step_name: str,
        error: str | dict[str, Any],
        next_retry_at: Optional[str] = None,
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] != "running":
                raise WorkflowTransitionError(f"retry_step not allowed from {step['status']}")
            code, message = self._normalize_error(error)
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='retrying', attempt_count=attempt_count + 1,
                    next_retry_at=COALESCE(?, CURRENT_TIMESTAMP), active_job_id=NULL,
                    error_code=?, error_message=?, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (next_retry_at, code, message, step["step_id"]),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                event_type="step_retry_scheduled",
                event_level="warning",
                payload={"step_name": step_name, "error_code": code, "error_message": message},
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def fail_step(
        self, run_id: str, step_name: str, error: str | dict[str, Any]
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] not in {"running", "retrying"}:
                raise WorkflowTransitionError(f"fail_step not allowed from {step['status']}")
            code, message = self._normalize_error(error)
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='failed', completed_at=CURRENT_TIMESTAMP, active_job_id=NULL,
                    error_code=?, error_message=?, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (code, message, step["step_id"]),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                event_type="step_failed",
                event_level="error",
                payload={"step_name": step_name, "error_code": code, "error_message": message},
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def cancel_step(
        self, run_id: str, step_name: str, reason: str = "run_cancelled"
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] == "cancelled":
                return self._step(step)
            if step["status"] not in {"running", "pending", "retrying"}:
                raise WorkflowTransitionError(f"cancel_step not allowed from {step['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='cancelled', completed_at=CURRENT_TIMESTAMP,
                    active_job_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (step["step_id"],),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                event_type="step_cancelled",
                payload={"step_name": step_name, "reason": reason},
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def skip_step(
        self, run_id: str, step_name: str, reason: str = "skipped"
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] != "pending":
                raise WorkflowTransitionError(f"skip_step not allowed from {step['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='skipped', completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (step["step_id"],),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                event_type="step_skipped",
                payload={"step_name": step_name, "reason": reason},
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def advance_to_next_step(self, run_id: str) -> WorkflowRun:
        async def op() -> WorkflowRun:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            self._ensure_not_terminal(run["status"], "advance_to_next_step")
            async with self._conn.execute(
                """
                SELECT *
                FROM workflow_steps
                WHERE run_id=? AND status IN ('pending', 'retrying')
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """,
                (run_id,),
            ) as cursor:
                next_step = await cursor.fetchone()
            if next_step is None:
                return self._run(run)
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET current_step=?, phase=?, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (next_step["step_name"], next_step["phase"], run_id),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=next_step["step_id"],
                event_type="run_advanced",
                payload={"current_step": next_step["step_name"]},
            )
            return self._run(await self._fetch_run_row(run_id))

        return await self._transaction(op)

    async def create_child_tasks(
        self,
        *,
        run_id: str,
        step_id: str,
        tasks: list[dict[str, Any]],
    ) -> list[WorkflowChildTask]:
        async def op() -> list[WorkflowChildTask]:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] in self.TERMINAL_RUN_STATUSES or run["status"] == "cancelling":
                raise WorkflowTransitionError(f"create_child_tasks not allowed from {run['status']}")
            step = await self._fetch_step_row_by_id(step_id)
            if step["run_id"] != run_id:
                raise WorkflowTransitionError("create_child_tasks step does not belong to run")

            created: list[WorkflowChildTask] = []
            for item in tasks:
                child_task_id = _new_id("child")
                await self._conn.execute(
                    """
                    INSERT INTO workflow_child_tasks (
                        child_task_id, run_id, step_id, task_type, slot_index, proposal_id,
                        status, max_attempts, input_hash, checkpoint_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        child_task_id,
                        run_id,
                        step_id,
                        str(item["task_type"]),
                        item.get("slot_index"),
                        item.get("proposal_id"),
                        int(item.get("max_attempts", 3)),
                        item.get("input_hash"),
                        _json_dump(item.get("checkpoint")) if item.get("checkpoint") is not None else None,
                    ),
                )
                created.append(self._child_task(await self._fetch_child_task_row(child_task_id)))
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step_id,
                event_type="child_tasks_created",
                payload={
                    "step_name": step["step_name"],
                    "child_task_ids": [task.child_task_id for task in created],
                    "count": len(created),
                },
            )
            return created

        return await self._transaction(op)

    async def start_child_task(self, child_task_id: str) -> WorkflowChildTask:
        async def op() -> WorkflowChildTask:
            assert self._conn is not None
            child = await self._fetch_child_task_row(child_task_id)
            run = await self._fetch_run_row(child["run_id"])
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(f"start_child_task requires running run, got {run['status']}")
            if child["status"] not in {"pending", "retrying"}:
                raise WorkflowTransitionError(f"start_child_task not allowed from {child['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='running', started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (child_task_id,),
            )
            await self._append_event(
                run_id=child["run_id"],
                thread_id=run["thread_id"],
                step_id=child["step_id"],
                child_task_id=child_task_id,
                event_type="child_task_started",
                payload={"child_task_id": child_task_id},
            )
            return self._child_task(await self._fetch_child_task_row(child_task_id))

        return await self._transaction(op)

    async def complete_child_task(
        self,
        child_task_id: str,
        artifact_refs: Optional[list[dict[str, Any]]] = None,
        note_id: Optional[str] = None,
    ) -> WorkflowChildTask:
        async def op() -> WorkflowChildTask:
            assert self._conn is not None
            child = await self._fetch_child_task_row(child_task_id)
            run = await self._fetch_run_row(child["run_id"])
            if run["status"] in {"cancelling", "cancelled"}:
                raise WorkflowTransitionError(f"complete_child_task not allowed from {run['status']}")
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(f"complete_child_task requires running run, got {run['status']}")
            if child["status"] != WorkflowStepStatus.RUNNING.value:
                raise WorkflowTransitionError(f"complete_child_task not allowed from {child['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='succeeded', output_artifact_refs_json=?, note_id=?,
                    error_code=NULL, error_message=NULL, completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (_json_dump(artifact_refs or []), note_id, child_task_id),
            )
            await self._append_event(
                run_id=child["run_id"],
                thread_id=run["thread_id"],
                step_id=child["step_id"],
                child_task_id=child_task_id,
                event_type="child_task_completed",
                payload={"child_task_id": child_task_id, "artifact_refs": artifact_refs or []},
            )
            return self._child_task(await self._fetch_child_task_row(child_task_id))

        return await self._transaction(op)

    async def retry_child_task(
        self, child_task_id: str, error: str | dict[str, Any]
    ) -> WorkflowChildTask:
        async def op() -> WorkflowChildTask:
            assert self._conn is not None
            child = await self._fetch_child_task_row(child_task_id)
            run = await self._fetch_run_row(child["run_id"])
            if child["status"] != WorkflowStepStatus.RUNNING.value:
                raise WorkflowTransitionError(f"retry_child_task not allowed from {child['status']}")
            code, message = self._normalize_error(error)
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='retrying', attempt_count=attempt_count + 1,
                    error_code=?, error_message=?, updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (code, message, child_task_id),
            )
            await self._append_event(
                run_id=child["run_id"],
                thread_id=run["thread_id"],
                step_id=child["step_id"],
                child_task_id=child_task_id,
                event_type="child_task_retry_scheduled",
                event_level="warning",
                payload={
                    "child_task_id": child_task_id,
                    "error_code": code,
                    "error_message": message,
                },
            )
            return self._child_task(await self._fetch_child_task_row(child_task_id))

        return await self._transaction(op)

    async def fail_child_task(
        self, child_task_id: str, error: str | dict[str, Any]
    ) -> WorkflowChildTask:
        async def op() -> WorkflowChildTask:
            assert self._conn is not None
            child = await self._fetch_child_task_row(child_task_id)
            run = await self._fetch_run_row(child["run_id"])
            if child["status"] not in {"running", "retrying"}:
                raise WorkflowTransitionError(f"fail_child_task not allowed from {child['status']}")
            code, message = self._normalize_error(error)
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='failed', error_code=?, error_message=?,
                    completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (code, message, child_task_id),
            )
            await self._append_event(
                run_id=child["run_id"],
                thread_id=run["thread_id"],
                step_id=child["step_id"],
                child_task_id=child_task_id,
                event_type="child_task_failed",
                event_level="error",
                payload={
                    "child_task_id": child_task_id,
                    "error_code": code,
                    "error_message": message,
                },
            )
            return self._child_task(await self._fetch_child_task_row(child_task_id))

        return await self._transaction(op)

    async def cancel_child_task(
        self, child_task_id: str, reason: str = "cancelled"
    ) -> WorkflowChildTask:
        async def op() -> WorkflowChildTask:
            assert self._conn is not None
            child = await self._fetch_child_task_row(child_task_id)
            run = await self._fetch_run_row(child["run_id"])
            if child["status"] == WorkflowStepStatus.CANCELLED.value:
                return self._child_task(child)
            if child["status"] not in {"pending", "running", "retrying"}:
                raise WorkflowTransitionError(f"cancel_child_task not allowed from {child['status']}")
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='cancelled', completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (child_task_id,),
            )
            await self._append_event(
                run_id=child["run_id"],
                thread_id=run["thread_id"],
                step_id=child["step_id"],
                child_task_id=child_task_id,
                event_type="child_task_cancelled",
                payload={"child_task_id": child_task_id, "reason": reason},
            )
            return self._child_task(await self._fetch_child_task_row(child_task_id))

        return await self._transaction(op)

    async def attach_artifact(
        self,
        *,
        run_id: str,
        artifact_type: WorkflowArtifactType | str,
        payload: Optional[dict[str, Any]] = None,
        storage_table: Optional[str] = None,
        storage_key: Optional[str] = None,
        summary_text: Optional[str] = None,
        created_by_step_id: Optional[str] = None,
        parent_artifact_id: Optional[str] = None,
        artifact_version: Optional[int] = None,
        payload_mode: WorkflowArtifactPayloadMode | str | None = None,
    ) -> WorkflowArtifact:
        async def op() -> WorkflowArtifact:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] in self.TERMINAL_RUN_STATUSES or run["status"] == "cancelling":
                raise WorkflowTransitionError(f"attach_artifact not allowed from {run['status']}")
            type_value = (
                artifact_type.value if isinstance(artifact_type, WorkflowArtifactType) else str(artifact_type)
            )
            mode_value = (
                payload_mode.value
                if isinstance(payload_mode, WorkflowArtifactPayloadMode)
                else payload_mode
            )
            if mode_value is None:
                mode_value = (
                    WorkflowArtifactPayloadMode.PATCH.value
                    if parent_artifact_id
                    else WorkflowArtifactPayloadMode.SNAPSHOT.value
                )
            next_version = await WorkflowArtifactVersionPolicy.allocate_artifact_version(
                self._conn,
                run_id=run_id,
                artifact_type=type_value,
                parent_artifact_id=parent_artifact_id,
                requested_version=artifact_version,
                fallback_version=int(run["artifact_version"]) + 1,
            )
            artifact_id = _new_id("artifact")
            await self._conn.execute(
                """
                INSERT INTO workflow_artifacts (
                    artifact_id, run_id, thread_id, artifact_type, artifact_version,
                    parent_artifact_id, status, payload_mode, storage_table, storage_key, payload_json,
                    summary_text, created_by_step_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    artifact_id,
                    run_id,
                    run["thread_id"],
                    type_value,
                    next_version,
                    parent_artifact_id,
                    mode_value,
                    storage_table,
                    storage_key,
                    _json_dump(payload) if payload is not None else None,
                    summary_text,
                    created_by_step_id,
                ),
            )
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET artifact_version=CASE
                        WHEN artifact_version < ? THEN ?
                        ELSE artifact_version
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (next_version, next_version, run_id),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=created_by_step_id,
                event_type="artifact_attached",
                payload={
                    "artifact_id": artifact_id,
                    "artifact_type": type_value,
                    "artifact_version": next_version,
                    "parent_artifact_id": parent_artifact_id,
                    "payload_mode": mode_value,
                },
            )
            async with self._conn.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            return self._artifact(row)

        return await self._transaction(op)

    async def add_constraint(
        self,
        *,
        run_id: str,
        message_id: str,
        raw_text: str,
        constraint_type: WorkflowConstraintType | str,
        scope: str,
        normalized_constraint: Optional[dict[str, Any]] = None,
        confidence: float = 1.0,
        target_artifact_id: Optional[str] = None,
        effective_from_step: Optional[str] = None,
        impact_level: str = "medium",
    ) -> WorkflowConstraint:
        async def op() -> WorkflowConstraint:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            status = run["status"]
            if status in self.TERMINAL_RUN_STATUSES or status == WorkflowRunStatus.CANCELLING.value:
                raise WorkflowTransitionError(f"add_constraint not allowed from {status}")

            next_version = int(run["constraint_version"]) + 1
            constraint_id = _new_id("constraint")
            type_value = (
                constraint_type.value
                if isinstance(constraint_type, WorkflowConstraintType)
                else str(constraint_type)
            )
            await self._conn.execute(
                """
                INSERT INTO workflow_constraints (
                    constraint_id, run_id, thread_id, message_id, constraint_version,
                    raw_text, constraint_type, scope, target_artifact_id,
                    effective_from_step, impact_level, status, confidence, normalized_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    constraint_id,
                    run_id,
                    run["thread_id"],
                    message_id,
                    next_version,
                    raw_text,
                    type_value,
                    scope,
                    target_artifact_id,
                    effective_from_step,
                    impact_level,
                    confidence,
                    _json_dump(normalized_constraint),
                ),
            )
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET constraint_version=?, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (next_version, run_id),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                event_type="constraint_added",
                payload={
                    "constraint_id": constraint_id,
                    "constraint_version": next_version,
                    "constraint_type": type_value,
                    "scope": scope,
                },
            )
            async with self._conn.execute(
                "SELECT * FROM workflow_constraints WHERE constraint_id=?",
                (constraint_id,),
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            return self._constraint(row)

        return await self._transaction(op)

    async def mark_constraint_applied(
        self,
        *,
        run_id: str,
        constraint_id: str,
        step_id: str,
    ) -> WorkflowConstraint:
        async def op() -> WorkflowConstraint:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            await self._conn.execute(
                """
                UPDATE workflow_constraints
                SET status='applied', effective_from_step=?, applied_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND constraint_id=?
                """,
                (step_id, run_id, constraint_id),
            )
            async with self._conn.execute(
                "SELECT * FROM workflow_constraints WHERE run_id=? AND constraint_id=?",
                (run_id, constraint_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise WorkflowTransitionError(f"Workflow constraint not found: {constraint_id}")
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step_id,
                event_type="constraint_applied",
                payload={"constraint_id": constraint_id},
            )
            return self._constraint(row)

        return await self._transaction(op)

    async def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        event_level: str = "info",
        step_id: Optional[str] = None,
        child_task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> WorkflowEvent:
        async def op() -> WorkflowEvent:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                event_type=event_type,
                payload=payload,
                event_level=event_level,
                step_id=step_id,
                child_task_id=child_task_id,
                job_id=job_id,
            )
            async with self._conn.execute(
                "SELECT * FROM workflow_events WHERE run_id=? ORDER BY event_id DESC LIMIT 1",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            return self._event(row)

        return await self._transaction(op)

    async def list_events(
        self,
        run_id: str,
        after_event_id: Optional[int] = None,
    ) -> list[WorkflowEvent]:
        async with WorkflowStore(self.db_path) as store:
            return await store.list_events(run_id, after_event_id=after_event_id)

    async def get_run_snapshot(self, run_id: str) -> dict[str, Any]:
        async with WorkflowStore(self.db_path) as store:
            run = await store.get_run(run_id)
            if run is None:
                raise WorkflowTransitionError(f"Workflow run not found: {run_id}")
            steps = await store.list_steps(run_id)
            child_tasks = await store.list_child_tasks(run_id)
            artifacts = await store.list_artifacts(run_id)
            constraints = await store.list_constraints(run_id)
        return {
            "run": run.model_dump(mode="json"),
            "steps": [step.model_dump(mode="json") for step in steps],
            "child_tasks": [task.model_dump(mode="json") for task in child_tasks],
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
            "constraints": [constraint.model_dump(mode="json") for constraint in constraints],
            "active_job": None,
        }

    @classmethod
    def _ensure_not_terminal(cls, status: str, action: str) -> None:
        if status in cls.TERMINAL_RUN_STATUSES:
            raise WorkflowTransitionError(f"{action} not allowed from terminal status {status}")
