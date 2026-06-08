"""SQLite-backed workflow schema and CRUD primitives."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

import aiosqlite

from app.config import settings
from app.logging_config import get_logger
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _json_load(value: Optional[str], fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


async def ensure_column(
    conn: aiosqlite.Connection,
    *,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    """Add a nullable compatibility column when an older SQLite table lacks it."""

    async with conn.execute(f"PRAGMA table_info({table_name})") as cursor:
        columns = {row[1] async for row in cursor}
    if column_name not in columns:
        await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


async def migrate_workflow_compat_columns(conn: aiosqlite.Connection) -> None:
    """Repair legacy tables with nullable workflow bridge columns."""

    await ensure_column(
        conn,
        table_name="creator_threads",
        column_name="active_run_id",
        column_sql="active_run_id TEXT",
    )
    for column_name in ("run_id", "step_id", "child_task_id"):
        await ensure_column(
            conn,
            table_name="jobs",
            column_name=column_name,
            column_sql=f"{column_name} TEXT",
        )


class WorkflowStore:
    """Persistence boundary for workflow tables introduced by restructure T1."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None
        self._logger = get_logger(__name__, component="workflow_store")

    async def __aenter__(self) -> "WorkflowStore":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self.initialize_schema()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def initialize_schema(self) -> None:
        assert self._conn is not None
        await self._create_workflow_tables()
        await self._conn.commit()

    async def _create_workflow_tables(self) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                phase TEXT NOT NULL DEFAULT 'intake',
                current_step TEXT,
                active_job_id TEXT,
                active_job_type TEXT,
                constraint_version INTEGER NOT NULL DEFAULT 0,
                artifact_version INTEGER NOT NULL DEFAULT 0,
                interrupt_policy TEXT NOT NULL DEFAULT 'safe_boundary',
                source_message_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                failed_at TIMESTAMP,
                cancelled_at TIMESTAMP,
                error_code TEXT,
                error_message TEXT
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_thread ON workflow_runs(thread_id, created_at DESC)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_steps (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
                step_name TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                input_hash TEXT,
                checkpoint_json TEXT,
                output_artifact_refs_json TEXT,
                active_job_id TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                next_retry_at TIMESTAMP,
                error_code TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_steps_run ON workflow_steps(run_id, step_name)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_child_tasks (
                child_task_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
                step_id TEXT NOT NULL REFERENCES workflow_steps(step_id),
                task_type TEXT NOT NULL,
                slot_index INTEGER,
                proposal_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                input_hash TEXT,
                checkpoint_json TEXT,
                output_artifact_refs_json TEXT,
                note_id TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                error_code TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_child_tasks_step ON workflow_child_tasks(step_id, slot_index)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
                thread_id TEXT NOT NULL,
                step_id TEXT,
                child_task_id TEXT,
                job_id TEXT,
                event_type TEXT NOT NULL,
                event_level TEXT NOT NULL DEFAULT 'info',
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_events_run ON workflow_events(run_id, event_id)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
                thread_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                artifact_version INTEGER NOT NULL DEFAULT 1,
                parent_artifact_id TEXT,
                status TEXT NOT NULL DEFAULT 'created',
                payload_mode TEXT NOT NULL DEFAULT 'snapshot',
                storage_table TEXT,
                storage_key TEXT,
                payload_json TEXT,
                summary_text TEXT,
                created_by_step_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_run ON workflow_artifacts(run_id, artifact_type, artifact_version)"
        )
        await ensure_column(
            self._conn,
            table_name="workflow_artifacts",
            column_name="payload_mode",
            column_sql="payload_mode TEXT NOT NULL DEFAULT 'snapshot'",
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_constraints (
                constraint_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
                thread_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                constraint_version INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                constraint_type TEXT NOT NULL,
                scope TEXT NOT NULL,
                target_artifact_id TEXT,
                effective_from_step TEXT,
                impact_level TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'active',
                confidence REAL NOT NULL DEFAULT 1.0,
                normalized_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied_at TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_constraints_run ON workflow_constraints(run_id, constraint_version)"
        )

    @staticmethod
    def _row_to_run(row: aiosqlite.Row) -> WorkflowRun:
        return WorkflowRun(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            user_id=row["user_id"],
            status=WorkflowRunStatus(row["status"]),
            phase=WorkflowPhase(row["phase"]),
            current_step=row["current_step"],
            active_job_id=row["active_job_id"],
            active_job_type=row["active_job_type"],
            constraint_version=int(row["constraint_version"]),
            artifact_version=int(row["artifact_version"]),
            interrupt_policy=row["interrupt_policy"],
            source_message_id=row["source_message_id"],
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
            started_at=_parse_dt(row["started_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            failed_at=_parse_dt(row["failed_at"]),
            cancelled_at=_parse_dt(row["cancelled_at"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    @staticmethod
    def _row_to_step(row: aiosqlite.Row) -> WorkflowStep:
        return WorkflowStep(
            step_id=row["step_id"],
            run_id=row["run_id"],
            step_name=row["step_name"],
            phase=WorkflowPhase(row["phase"]),
            status=WorkflowStepStatus(row["status"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            input_hash=row["input_hash"],
            checkpoint_json=_json_load(row["checkpoint_json"], None),
            output_artifact_refs_json=_json_load(row["output_artifact_refs_json"], None),
            active_job_id=row["active_job_id"],
            started_at=_parse_dt(row["started_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            next_retry_at=_parse_dt(row["next_retry_at"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
        )

    @staticmethod
    def _row_to_child_task(row: aiosqlite.Row) -> WorkflowChildTask:
        return WorkflowChildTask(
            child_task_id=row["child_task_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            task_type=row["task_type"],
            slot_index=row["slot_index"],
            proposal_id=row["proposal_id"],
            status=WorkflowStepStatus(row["status"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            input_hash=row["input_hash"],
            checkpoint_json=_json_load(row["checkpoint_json"], None),
            output_artifact_refs_json=_json_load(row["output_artifact_refs_json"], None),
            note_id=row["note_id"],
            started_at=_parse_dt(row["started_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
        )

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> WorkflowEvent:
        return WorkflowEvent(
            event_id=int(row["event_id"]),
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            step_id=row["step_id"],
            child_task_id=row["child_task_id"],
            job_id=row["job_id"],
            event_type=row["event_type"],
            event_level=row["event_level"],
            payload_json=_json_load(row["payload_json"], {}),
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
        )

    @staticmethod
    def _row_to_artifact(row: aiosqlite.Row) -> WorkflowArtifact:
        return WorkflowArtifact(
            artifact_id=row["artifact_id"],
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            artifact_type=WorkflowArtifactType(row["artifact_type"]),
            artifact_version=int(row["artifact_version"]),
            parent_artifact_id=row["parent_artifact_id"],
            status=row["status"],
            payload_mode=WorkflowArtifactPayloadMode(row["payload_mode"] or "snapshot"),
            storage_table=row["storage_table"],
            storage_key=row["storage_key"],
            payload_json=_json_load(row["payload_json"], None),
            summary_text=row["summary_text"],
            created_by_step_id=row["created_by_step_id"],
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
        )

    @staticmethod
    def _row_to_constraint(row: aiosqlite.Row) -> WorkflowConstraint:
        return WorkflowConstraint(
            constraint_id=row["constraint_id"],
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            message_id=row["message_id"],
            constraint_version=int(row["constraint_version"]),
            raw_text=row["raw_text"],
            constraint_type=WorkflowConstraintType(row["constraint_type"]),
            scope=row["scope"],
            target_artifact_id=row["target_artifact_id"],
            effective_from_step=row["effective_from_step"],
            impact_level=row["impact_level"],
            status=row["status"],
            confidence=float(row["confidence"]),
            normalized_json=_json_load(row["normalized_json"], {}),
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
            applied_at=_parse_dt(row["applied_at"]),
        )

    async def create_run(
        self,
        *,
        thread_id: str,
        user_id: str,
        source_message_id: Optional[str] = None,
    ) -> WorkflowRun:
        assert self._conn is not None
        run_id = _new_id("run")
        await self._conn.execute(
            """
            INSERT INTO workflow_runs (
                run_id, thread_id, user_id, status, phase, interrupt_policy,
                source_message_id
            )
            VALUES (?, ?, ?, 'created', 'intake', 'safe_boundary', ?)
            """,
            (run_id, thread_id, user_id, source_message_id),
        )
        await self._conn.commit()
        run = await self.get_run(run_id)
        assert run is not None
        return run

    async def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)) as cursor:
            row = await cursor.fetchone()
        return self._row_to_run(row) if row is not None else None

    async def create_step(
        self,
        *,
        run_id: str,
        step_name: str,
        phase: WorkflowPhase | str,
        max_attempts: int = 3,
        checkpoint: Optional[dict[str, Any]] = None,
    ) -> WorkflowStep:
        assert self._conn is not None
        step_id = _new_id("step")
        phase_value = phase.value if isinstance(phase, WorkflowPhase) else phase
        await self._conn.execute(
            """
            INSERT INTO workflow_steps (
                step_id, run_id, step_name, phase, status, max_attempts, checkpoint_json
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (step_id, run_id, step_name, phase_value, max_attempts, _json_dump(checkpoint) if checkpoint else None),
        )
        await self._conn.commit()
        step = await self.get_step(step_id)
        assert step is not None
        return step

    async def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM workflow_steps WHERE step_id = ?", (step_id,)) as cursor:
            row = await cursor.fetchone()
        return self._row_to_step(row) if row is not None else None

    async def list_steps(self, run_id: str) -> list[WorkflowStep]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM workflow_steps
            WHERE run_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_step(row) for row in rows]

    async def create_child_task(
        self,
        *,
        run_id: str,
        step_id: str,
        task_type: str,
        slot_index: Optional[int] = None,
        proposal_id: Optional[str] = None,
        max_attempts: int = 3,
    ) -> WorkflowChildTask:
        assert self._conn is not None
        child_task_id = _new_id("child")
        await self._conn.execute(
            """
            INSERT INTO workflow_child_tasks (
                child_task_id, run_id, step_id, task_type, slot_index, proposal_id,
                status, max_attempts
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (child_task_id, run_id, step_id, task_type, slot_index, proposal_id, max_attempts),
        )
        await self._conn.commit()
        child_task = await self.get_child_task(child_task_id)
        assert child_task is not None
        return child_task

    async def get_child_task(self, child_task_id: str) -> Optional[WorkflowChildTask]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM workflow_child_tasks WHERE child_task_id = ?", (child_task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_child_task(row) if row is not None else None

    async def list_child_tasks(self, run_id: str) -> list[WorkflowChildTask]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM workflow_child_tasks
            WHERE run_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_child_task(row) for row in rows]

    async def append_event(
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
    ) -> WorkflowEvent:
        assert self._conn is not None
        async with self._conn.execute(
            """
            INSERT INTO workflow_events (
                run_id, thread_id, step_id, child_task_id, job_id,
                event_type, event_level, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
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
        ) as cursor:
            row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return self._row_to_event(row)

    async def list_events(
        self,
        run_id: str,
        *,
        after_event_id: Optional[int] = None,
    ) -> list[WorkflowEvent]:
        assert self._conn is not None
        params: list[Any] = [run_id]
        sql = "SELECT * FROM workflow_events WHERE run_id = ?"
        if after_event_id is not None:
            sql += " AND event_id > ?"
            params.append(after_event_id)
        sql += " ORDER BY event_id ASC"
        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def create_artifact(
        self,
        *,
        run_id: str,
        thread_id: str,
        artifact_type: WorkflowArtifactType | str,
        artifact_version: int = 1,
        parent_artifact_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        payload_mode: WorkflowArtifactPayloadMode | str = WorkflowArtifactPayloadMode.SNAPSHOT,
        storage_table: Optional[str] = None,
        storage_key: Optional[str] = None,
        summary_text: Optional[str] = None,
        created_by_step_id: Optional[str] = None,
    ) -> WorkflowArtifact:
        assert self._conn is not None
        artifact_id = _new_id("artifact")
        type_value = artifact_type.value if isinstance(artifact_type, WorkflowArtifactType) else artifact_type
        mode_value = payload_mode.value if isinstance(payload_mode, WorkflowArtifactPayloadMode) else str(payload_mode)
        await self._conn.execute(
            """
            INSERT INTO workflow_artifacts (
                artifact_id, run_id, thread_id, artifact_type, artifact_version,
                parent_artifact_id, status, payload_mode, storage_table, storage_key, payload_json,
                summary_text, created_by_step_id
            )
            VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                run_id,
                thread_id,
                type_value,
                artifact_version,
                parent_artifact_id,
                mode_value,
                storage_table,
                storage_key,
                _json_dump(payload) if payload is not None else None,
                summary_text,
                created_by_step_id,
            ),
        )
        await self._conn.commit()
        artifact = await self.get_artifact(artifact_id)
        assert artifact is not None
        return artifact

    async def get_artifact(self, artifact_id: str) -> Optional[WorkflowArtifact]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM workflow_artifacts WHERE artifact_id = ?", (artifact_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_artifact(row) if row is not None else None

    async def list_artifacts(self, run_id: str) -> list[WorkflowArtifact]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM workflow_artifacts
            WHERE run_id = ?
            ORDER BY artifact_version ASC, created_at ASC, rowid ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_artifact(row) for row in rows]

    async def list_artifacts_by_type(self, artifact_type: WorkflowArtifactType | str) -> list[WorkflowArtifact]:
        assert self._conn is not None
        type_value = artifact_type.value if isinstance(artifact_type, WorkflowArtifactType) else str(artifact_type)
        async with self._conn.execute(
            """
            SELECT *
            FROM workflow_artifacts
            WHERE artifact_type = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (type_value,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_artifact(row) for row in rows]

    async def update_artifact_status(self, artifact_id: str, status: str) -> Optional[WorkflowArtifact]:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE workflow_artifacts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE artifact_id = ?",
            (status, artifact_id),
        )
        await self._conn.commit()
        return await self.get_artifact(artifact_id)

    async def create_constraint(
        self,
        *,
        run_id: str,
        thread_id: str,
        message_id: str,
        raw_text: str,
        constraint_type: WorkflowConstraintType | str,
        scope: str,
        constraint_version: int = 1,
        normalized: Optional[dict[str, Any]] = None,
        confidence: float = 1.0,
    ) -> WorkflowConstraint:
        assert self._conn is not None
        constraint_id = _new_id("constraint")
        type_value = constraint_type.value if isinstance(constraint_type, WorkflowConstraintType) else constraint_type
        await self._conn.execute(
            """
            INSERT INTO workflow_constraints (
                constraint_id, run_id, thread_id, message_id, constraint_version,
                raw_text, constraint_type, scope, impact_level, status, confidence,
                normalized_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'medium', 'active', ?, ?)
            """,
            (
                constraint_id,
                run_id,
                thread_id,
                message_id,
                constraint_version,
                raw_text,
                type_value,
                scope,
                confidence,
                _json_dump(normalized),
            ),
        )
        await self._conn.commit()
        constraint = await self.get_constraint(constraint_id)
        assert constraint is not None
        return constraint

    async def get_constraint(self, constraint_id: str) -> Optional[WorkflowConstraint]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM workflow_constraints WHERE constraint_id = ?", (constraint_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_constraint(row) if row is not None else None

    async def list_constraints(self, run_id: str) -> list[WorkflowConstraint]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM workflow_constraints
            WHERE run_id = ?
            ORDER BY constraint_version ASC, created_at ASC, rowid ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_constraint(row) for row in rows]
