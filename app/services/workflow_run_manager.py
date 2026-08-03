"""Workflow state-transition manager.

This module is the single write boundary for workflow run/step state. External
callers should report commands or transitions here instead of mutating workflow
tables directly.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Optional, TypeVar

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

_TIMING_INTERVAL_KEYS = (
    "queue_spans",
    "execution_spans",
    "retry_backoff_spans",
    "waiting_spans",
    "pause_spans",
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timing_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    raw = row["timing_json"]
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _open_interval(timing: dict[str, Any], key: str, at: str) -> None:
    intervals = timing.setdefault(key, [])
    if not isinstance(intervals, list):
        intervals = []
        timing[key] = intervals
    if intervals and isinstance(intervals[-1], dict) and intervals[-1].get("finished_at") is None:
        return
    intervals.append({"started_at": at, "finished_at": None})


def _close_interval(timing: dict[str, Any], key: str, at: str) -> None:
    intervals = timing.get(key)
    if not isinstance(intervals, list):
        return
    for interval in reversed(intervals):
        if isinstance(interval, dict) and interval.get("finished_at") is None:
            interval["finished_at"] = at
            return


def _queue_timing(timing: dict[str, Any], at: str) -> dict[str, Any]:
    timing.setdefault("queued_at", at)
    _open_interval(timing, "queue_spans", at)
    return timing


def _start_timing_execution(timing: dict[str, Any], at: str) -> dict[str, Any]:
    if not isinstance(timing.get("execution_spans"), list) or not timing["execution_spans"]:
        _queue_timing(timing, at)
    _close_interval(timing, "queue_spans", at)
    _close_interval(timing, "retry_backoff_spans", at)
    _close_interval(timing, "waiting_spans", at)
    _close_interval(timing, "pause_spans", at)
    timing.pop("waiting_started_at", None)
    timing.pop("retry_backoff_started_at", None)
    _open_interval(timing, "execution_spans", at)
    return timing


def _stop_timing_execution(timing: dict[str, Any], at: str) -> dict[str, Any]:
    _close_interval(timing, "execution_spans", at)
    return timing


def _close_all_timing_intervals(timing: dict[str, Any], at: str) -> dict[str, Any]:
    for key in _TIMING_INTERVAL_KEYS:
        _close_interval(timing, key, at)
    return timing


def _timing_json(timing: dict[str, Any]) -> str:
    return _json_dump(timing)


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
        self._transaction_depth = 0

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
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _transaction(self, fn: Callable[[], Awaitable[T]]) -> T:
        assert self._conn is not None
        if self._transaction_depth:
            return await fn()
        await self._conn.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            result = await fn()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            self._transaction_depth -= 1
        await self._conn.commit()
        return result

    async def complete_brief_and_plan_atomically(
        self,
        *,
        workflow_run_id: str,
        task_specs: list[dict],
        confirmation_writer: Callable[[aiosqlite.Connection, list[str]], Awaitable[None]],
    ) -> list[str]:
        """Commit workflow transitions and confirmation facts in one transaction."""

        async def op() -> list[str]:
            brief = await self._fetch_step_row(workflow_run_id, "brief_confirm")
            brief_timing = _timing_from_row(brief)
            await self.start_step(
                workflow_run_id,
                "brief_confirm",
                record_execution=not bool(brief_timing.get("execution_spans")),
            )
            await self.complete_step(
                workflow_run_id,
                "brief_confirm",
                artifact_refs=[{"type": "content_research_brief_confirmed"}],
            )
            await self.advance_to_next_step(workflow_run_id)
            await self.start_step(workflow_run_id, "plan_build")
            await self.complete_step(
                workflow_run_id,
                "plan_build",
                artifact_refs=[
                    {"type": "content_research_plan"},
                    {"type": "content_research_subagent_task_specs", "count": len(task_specs)},
                ],
            )
            await self.advance_to_next_step(workflow_run_id)
            formal_step = await self.start_step(workflow_run_id, "formal_research")
            child_tasks = await self.create_child_tasks(
                run_id=workflow_run_id,
                step_id=formal_step.step_id,
                tasks=[
                    {
                        "task_type": str(
                            spec.get("task_type") or "content_research_source_collect"
                        ),
                        "slot_index": spec.get("sequence_no"),
                        "max_attempts": 3,
                        "checkpoint": {"direction_id": spec.get("direction_id")},
                    }
                    for spec in task_specs
                ],
            )
            await confirmation_writer(self._conn, [task.child_task_id for task in child_tasks])
            return [task.child_task_id for task in child_tasks]

        return await self._transaction(op)

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

    async def _has_auth_required_child(self, run_id: str) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT 1 FROM workflow_child_tasks
            WHERE run_id = ? AND status = 'failed'
              AND error_code IN ('auth_required', 'auth_expired')
            LIMIT 1
            """,
            (run_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

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
            if status == WorkflowRunStatus.RUNNING.value:
                if not await self._has_auth_required_child(run_id):
                    raise WorkflowTransitionError(
                        "resume_run for a running run requires an auth-required child task"
                    )
                # A provider may require interactive authentication while the
                # parent orchestration remains running.  This is a safe,
                # idempotent wake-up: do not rewrite the run state, only
                # notify any persisted jobs that authentication completed.
                resumed_jobs = await self._resume_workflow_jobs_if_present(run_id)
                await self._conn.execute(
                    "UPDATE workflow_runs SET updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                    (run_id,),
                )
                await self._append_event(
                    run_id=run_id,
                    thread_id=row["thread_id"],
                    event_type="run_resume_requested",
                    payload={"resumed_job_count": resumed_jobs, "already_running": True},
                )
                return self._run(await self._fetch_run_row(run_id))
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

    async def wait_for_user_recovery(
        self,
        run_id: str,
        *,
        step_name: str,
        reason: str | dict[str, Any],
        state_writer: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None,
    ) -> WorkflowRun:
        """Stop a recoverable step at a durable, user-resumable boundary.

        A provider failure is not a successful completion and must not leave
        the parent run marked ``running`` after its worker returns.  The step
        becomes retryable while the run records that progress now depends on a
        user action (for example, retrying after a transient provider error).
        """

        async def op() -> WorkflowRun:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(
                    f"wait_for_user_recovery requires running run, got {run['status']}"
                )
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] != WorkflowStepStatus.RUNNING.value:
                raise WorkflowTransitionError(
                    f"wait_for_user_recovery not allowed from {step['status']}"
                )
            if state_writer is not None:
                await state_writer(self._conn)
            code, message = self._normalize_error(reason)
            waiting_at = _utc_timestamp()
            timing = _stop_timing_execution(_timing_from_row(step), waiting_at)
            _open_interval(timing, "waiting_spans", waiting_at)
            timing["waiting_started_at"] = waiting_at
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='retrying', attempt_count=attempt_count + 1,
                    next_retry_at=NULL, active_job_id=NULL,
                    error_code=?, error_message=?, timing_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (code, message, _timing_json(timing), step["step_id"]),
            )
            await self._conn.execute(
                """
                UPDATE workflow_runs
                SET status='waiting_user', active_job_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=?
                """,
                (run_id,),
            )
            await self._append_event(
                run_id=run_id,
                thread_id=run["thread_id"],
                step_id=step["step_id"],
                event_type="run_waiting_user",
                event_level="warning",
                payload={
                    "step_name": step_name,
                    "reason_code": code,
                    "reason_message": message,
                    "recovery_required": True,
                },
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
            cancelled_at = _utc_timestamp()
            async with self._conn.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? AND status IN ('pending', 'retrying')",
                (run_id,),
            ) as cursor:
                cancellable_steps = await cursor.fetchall()
            for step in cancellable_steps:
                timing = _close_all_timing_intervals(_timing_from_row(step), cancelled_at)
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='cancelled', timing_json=?, completed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=?
                    """,
                    (_timing_json(timing), step["step_id"]),
                )
            async with self._conn.execute(
                """
                SELECT * FROM workflow_child_tasks
                WHERE run_id=? AND status IN ('pending', 'running', 'retrying')
                """,
                (run_id,),
            ) as cursor:
                cancellable_children = await cursor.fetchall()
            for child in cancellable_children:
                timing = _close_all_timing_intervals(_timing_from_row(child), cancelled_at)
                await self._conn.execute(
                    """
                    UPDATE workflow_child_tasks
                    SET status='cancelled', timing_json=?, completed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE child_task_id=?
                    """,
                    (_timing_json(timing), child["child_task_id"]),
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
                raise WorkflowTransitionError(
                    f"ack_pause_at_boundary not allowed from {run['status']}"
                )
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] == WorkflowStepStatus.RUNNING.value:
                paused_at = _utc_timestamp()
                timing = _stop_timing_execution(_timing_from_row(step), paused_at)
                _open_interval(timing, "pause_spans", paused_at)
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='retrying', attempt_count=attempt_count + 1,
                        active_job_id=NULL, next_retry_at=CURRENT_TIMESTAMP,
                        error_code='RUN_PAUSED', error_message='run paused at safe boundary',
                        timing_json=?, updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=?
                    """,
                    (_timing_json(timing), step["step_id"]),
                )
                async with self._conn.execute(
                    """
                    SELECT * FROM workflow_child_tasks
                    WHERE run_id=? AND step_id=? AND status='running'
                    """,
                    (run_id, step["step_id"]),
                ) as cursor:
                    running_children = await cursor.fetchall()
                for child in running_children:
                    child_timing = _stop_timing_execution(
                        _timing_from_row(child), paused_at
                    )
                    _open_interval(child_timing, "pause_spans", paused_at)
                    await self._conn.execute(
                        """
                        UPDATE workflow_child_tasks
                        SET status='retrying', error_code='RUN_PAUSED',
                            error_message='run paused at safe boundary', timing_json=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE child_task_id=?
                        """,
                        (_timing_json(child_timing), child["child_task_id"]),
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
                raise WorkflowTransitionError(
                    f"ack_cancel_at_boundary not allowed from {run['status']}"
                )
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] in {"pending", "running", "retrying"}:
                timing = _close_all_timing_intervals(
                    _timing_from_row(step), _utc_timestamp()
                )
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='cancelled', active_job_id=NULL, timing_json=?,
                        completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=?
                    """,
                    (_timing_json(timing), step["step_id"]),
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
                timing = _queue_timing({}, _utc_timestamp())
                await self._conn.execute(
                    """
                    INSERT INTO workflow_steps (
                        step_id, run_id, step_name, phase, status, max_attempts,
                        input_hash, checkpoint_json, timing_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        step_id,
                        run_id,
                        step_name,
                        phase_value,
                        max_attempts,
                        input_hash,
                        _json_dump(checkpoint) if checkpoint is not None else None,
                        _timing_json(timing),
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
        self,
        run_id: str,
        step_name: str,
        job_id: Optional[str] = None,
        *,
        record_execution: bool = True,
    ) -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(
                    f"start_step requires running run, got {run['status']}"
                )
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] not in {"pending", "retrying"}:
                raise WorkflowTransitionError(f"start_step not allowed from {step['status']}")
            timing = _timing_from_row(step)
            if record_execution:
                timing = _start_timing_execution(timing, _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='running', active_job_id=?, timing_json=?, started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (job_id, _timing_json(timing), step["step_id"]),
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

    async def record_step_execution_started(
        self, run_id: str, step_name: str
    ) -> WorkflowStep:
        """Record a real server-side work boundary before deferred state writes.

        Content Research builds its confirmation and plan objects before their
        final atomic persistence. This method captures that real work start
        without advancing the workflow state machine early; a later
        ``start_step`` reuses the open execution span.
        """

        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(
                    f"record_step_execution_started requires running run, got {run['status']}"
                )
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] not in {"pending", "running", "retrying"}:
                raise WorkflowTransitionError(
                    f"record_step_execution_started not allowed from {step['status']}"
                )
            timing = _start_timing_execution(_timing_from_row(step), _utc_timestamp())
            await self._conn.execute(
                "UPDATE workflow_steps SET timing_json=?, updated_at=CURRENT_TIMESTAMP WHERE step_id=?",
                (_timing_json(timing), step["step_id"]),
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def record_step_execution_finished(
        self, run_id: str, step_name: str
    ) -> WorkflowStep:
        """Close pre-transition work without changing the workflow state."""

        async def op() -> WorkflowStep:
            assert self._conn is not None
            await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            timing = _stop_timing_execution(_timing_from_row(step), _utc_timestamp())
            await self._conn.execute(
                "UPDATE workflow_steps SET timing_json=?, updated_at=CURRENT_TIMESTAMP WHERE step_id=?",
                (_timing_json(timing), step["step_id"]),
            )
            return self._step(await self._fetch_step_row(run_id, step_name))

        return await self._transaction(op)

    async def abort_step_execution(self, run_id: str, step_name: str) -> WorkflowStep:
        """Abort an in-flight pre-transition span while leaving the step retryable."""
        return await self.record_step_execution_finished(run_id, step_name)

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
                timing = _close_all_timing_intervals(
                    _timing_from_row(step), _utc_timestamp()
                )
                await self._conn.execute(
                    """
                    UPDATE workflow_steps
                    SET status='cancelled', timing_json=?, completed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE step_id=? AND status='running'
                    """,
                    (_timing_json(timing), step["step_id"]),
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
                raise WorkflowTransitionError(
                    f"complete_step requires running run, got {run['status']}"
                )
            if step["status"] != "running":
                raise WorkflowTransitionError(f"complete_step not allowed from {step['status']}")
            timing = _close_all_timing_intervals(_timing_from_row(step), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='succeeded', output_artifact_refs_json=?, active_job_id=NULL, timing_json=?,
                    completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (json.dumps(artifact_refs or [], ensure_ascii=False, default=str), _timing_json(timing), step["step_id"]),
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
            retry_at = _utc_timestamp()
            timing = _stop_timing_execution(_timing_from_row(step), retry_at)
            _open_interval(timing, "retry_backoff_spans", retry_at)
            timing["retry_backoff_started_at"] = retry_at
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='retrying', attempt_count=attempt_count + 1,
                    next_retry_at=COALESCE(?, CURRENT_TIMESTAMP), active_job_id=NULL,
                    error_code=?, error_message=?, timing_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (next_retry_at, code, message, _timing_json(timing), step["step_id"]),
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
            timing = _close_all_timing_intervals(_timing_from_row(step), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='failed', completed_at=CURRENT_TIMESTAMP, active_job_id=NULL, timing_json=?,
                    error_code=?, error_message=?, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (_timing_json(timing), code, message, step["step_id"]),
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
            timing = _stop_timing_execution(_timing_from_row(step), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='cancelled', timing_json=?, completed_at=CURRENT_TIMESTAMP,
                    active_job_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (_timing_json(timing), step["step_id"]),
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

    async def skip_step(self, run_id: str, step_name: str, reason: str = "skipped") -> WorkflowStep:
        async def op() -> WorkflowStep:
            assert self._conn is not None
            run = await self._fetch_run_row(run_id)
            step = await self._fetch_step_row(run_id, step_name)
            if step["status"] != "pending":
                raise WorkflowTransitionError(f"skip_step not allowed from {step['status']}")
            timing = _close_all_timing_intervals(_timing_from_row(step), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_steps
                SET status='skipped', timing_json=?, completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE step_id=?
                """,
                (_timing_json(timing), step["step_id"]),
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
                raise WorkflowTransitionError(
                    f"create_child_tasks not allowed from {run['status']}"
                )
            step = await self._fetch_step_row_by_id(step_id)
            if step["run_id"] != run_id:
                raise WorkflowTransitionError("create_child_tasks step does not belong to run")

            created: list[WorkflowChildTask] = []
            for item in tasks:
                child_task_id = _new_id("child")
                timing = _queue_timing({}, _utc_timestamp())
                await self._conn.execute(
                    """
                    INSERT INTO workflow_child_tasks (
                        child_task_id, run_id, step_id, task_type, slot_index, proposal_id,
                        status, max_attempts, input_hash, checkpoint_json, timing_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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
                        _json_dump(item.get("checkpoint"))
                        if item.get("checkpoint") is not None
                        else None,
                        _timing_json(timing),
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
                raise WorkflowTransitionError(
                    f"start_child_task requires running run, got {run['status']}"
                )
            if child["status"] not in {"pending", "retrying"}:
                raise WorkflowTransitionError(
                    f"start_child_task not allowed from {child['status']}"
                )
            timing = _start_timing_execution(_timing_from_row(child), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='running', timing_json=?, started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (_timing_json(timing), child_task_id),
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
                raise WorkflowTransitionError(
                    f"complete_child_task not allowed from {run['status']}"
                )
            if run["status"] != WorkflowRunStatus.RUNNING.value:
                raise WorkflowTransitionError(
                    f"complete_child_task requires running run, got {run['status']}"
                )
            if child["status"] != WorkflowStepStatus.RUNNING.value:
                raise WorkflowTransitionError(
                    f"complete_child_task not allowed from {child['status']}"
                )
            timing = _close_all_timing_intervals(_timing_from_row(child), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='succeeded', output_artifact_refs_json=?, note_id=?, timing_json=?,
                    error_code=NULL, error_message=NULL, completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (_json_dump(artifact_refs or []), note_id, _timing_json(timing), child_task_id),
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
            # A content-research specialist may have reached a durable failed
            # terminal state while its parent research step remains open for a
            # user-triggered retry.  Requeueing it preserves the same child ID
            # and its event history instead of creating a duplicate task.
            if child["status"] not in {
                WorkflowStepStatus.RUNNING.value,
                WorkflowStepStatus.FAILED.value,
            }:
                raise WorkflowTransitionError(
                    f"retry_child_task not allowed from {child['status']}"
                )
            if int(child["attempt_count"]) >= max(int(child["max_attempts"]) - 1, 0):
                raise WorkflowTransitionError(
                    "retry_child_task attempt budget exhausted: "
                    f"{child['attempt_count']} recoveries for "
                    f"{child['max_attempts']} total attempts"
                )
            code, message = self._normalize_error(error)
            retry_at = _utc_timestamp()
            timing = _stop_timing_execution(_timing_from_row(child), retry_at)
            _open_interval(timing, "retry_backoff_spans", retry_at)
            timing["retry_backoff_started_at"] = retry_at
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='retrying', attempt_count=attempt_count + 1,
                    error_code=?, error_message=?, timing_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (code, message, _timing_json(timing), child_task_id),
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

    async def restart_step_and_retry_children(
        self,
        run_id: str,
        *,
        step_name: str,
        child_task_ids: list[str],
        resume_parent: bool = True,
    ) -> tuple[WorkflowRun | None, list[WorkflowChildTask]]:
        """Atomically resume a retryable step and consume its child budgets."""

        async def op() -> tuple[WorkflowRun | None, list[WorkflowChildTask]]:
            run = None
            if resume_parent:
                run = await self.resume_run(run_id)
                await self.start_step(run_id, step_name)
            children = [
                await self.retry_child_task(
                    child_task_id,
                    {
                        "code": "user_recovery",
                        "message": "User requested same-run specialist recovery.",
                    },
                )
                for child_task_id in child_task_ids
            ]
            return run, children

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
            timing = _close_all_timing_intervals(_timing_from_row(child), _utc_timestamp())
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='failed', timing_json=?, error_code=?, error_message=?,
                    completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (_timing_json(timing), code, message, child_task_id),
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
                raise WorkflowTransitionError(
                    f"cancel_child_task not allowed from {child['status']}"
                )
            timing = _close_all_timing_intervals(
                _timing_from_row(child), _utc_timestamp()
            )
            await self._conn.execute(
                """
                UPDATE workflow_child_tasks
                SET status='cancelled', timing_json=?, completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE child_task_id=?
                """,
                (_timing_json(timing), child_task_id),
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
            type_value = (
                artifact_type.value
                if isinstance(artifact_type, WorkflowArtifactType)
                else str(artifact_type)
            )
            # A terminal successful run may append its single final result.
            # This preserves the Creator contract (complete first, then emit
            # artifact_result) without reopening a completed workflow.
            terminal_final_result = (
                run["status"] == WorkflowRunStatus.SUCCEEDED.value
                and type_value == WorkflowArtifactType.FINAL_RESULT.value
            )
            if (
                run["status"] in self.TERMINAL_RUN_STATUSES or run["status"] == "cancelling"
            ) and not terminal_final_result:
                raise WorkflowTransitionError(f"attach_artifact not allowed from {run['status']}")
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
