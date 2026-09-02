"""Single mutation and read boundary for the Content Research lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

import aiosqlite

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.contracts import DIRECTION_CATALOG_V1, build_default_snapshot
from app.content_research.lifecycle.models import (
    ContentResearchState,
    ExecutionEvent,
    LifecycleCommand,
    RunProjection,
)
from app.content_research.lifecycle.projection import projection_from_row
from app.content_research.lifecycle.transitions import (
    LifecycleTransitionError,
    transition,
)
from app.content_research.scope_contract import (
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.workflow.direction_registry import ResearchDirectionRegistry
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    RuntimeWriteCoordinator,
    TypedMutation,
)
from app.core.runtime_write_registry import get_runtime_writer
from app.core.sqlite_connection_roles import (
    open_bootstrap_async_database,
    open_readonly_async_database,
    open_readonly_database,
)
from app.memory.workflow_store import WorkflowStore


class LifecycleCommandConflict(ValueError):
    """A command targeted stale or mismatched lifecycle authority."""


class LifecyclePersistenceBusy(RuntimeError):
    """The bounded local SQLite contention budget was exhausted."""


TraceSnapshotT = TypeVar("TraceSnapshotT")

_RECOVERY_COMMANDS = {
    "retry_presearch",
    "retry_retrieval",
    "retry_analysis",
    "retry_report",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _publication_id_from_artifact_payload(value: Any) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    publication_id = payload.get("report_publication_id")
    return publication_id if isinstance(publication_id, str) and publication_id else None


def _fingerprint(command: LifecycleCommand) -> str:
    payload = {
        "run_id": command.run_id,
        "expected_state": command.expected_state.value if command.expected_state else None,
        "expected_revision": command.expected_revision,
        "kind": command.kind,
        "payload": dict(command.payload),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class ContentResearchPersistenceCoordinator:
    """Own lifecycle transactions; callers cannot partially advance a Run."""

    def __init__(
        self,
        db_path: str,
        *,
        writer: RuntimeWriteCoordinator | None = None,
    ) -> None:
        self._db_path = db_path
        self._writer = writer or get_runtime_writer(self._db_path)
        self._borrowed_connection: Any | None = None
        self._writer_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()
        self._schema_ready = self._writer is not None

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            async with WorkflowStore(self._db_path):
                pass
            bootstrap_content_research_schema(self._db_path)
            self._schema_ready = True

    async def _connect(self) -> aiosqlite.Connection:
        if self._borrowed_connection is not None:
            return self._borrowed_connection
        if self._writer is not None:
            conn = await open_readonly_async_database(self._db_path, timeout=0.25)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            return conn
        conn = await open_bootstrap_async_database(self._db_path, timeout=0.25)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout=250")
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _is_sqlite_busy(exc: BaseException) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    async def _with_busy_retry(self, operation):
        for attempt in range(1, 4):
            try:
                return await operation()
            except (sqlite3.OperationalError, aiosqlite.OperationalError) as exc:
                if not self._is_sqlite_busy(exc):
                    raise
                if attempt == 3:
                    raise LifecyclePersistenceBusy(
                        "LOCAL_PERSISTENCE_BUSY after 3 attempts"
                    ) from exc
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    async def apply(self, command: LifecycleCommand) -> RunProjection:
        if self._writer is not None:
            projection, _ = await self._submit_lifecycle("apply", command)
            return projection
        await self._ensure_schema()
        async with self._writer_lock:
            return await self._with_busy_retry(lambda: self._apply_once(command))

    async def retry_analysis(
        self,
        command: LifecycleCommand,
        *,
        expected_attempt_id: str,
        expected_contract_fingerprint: str,
    ) -> tuple[RunProjection, str]:
        """Atomically advance recovery and create the exact analysis successor."""
        if command.kind != "retry_analysis":
            raise LifecycleCommandConflict("analysis retry requires retry_analysis command")
        if self._writer is not None:
            projection, attempt_id = await self._submit_lifecycle(
                "retry_analysis",
                command,
                expected_attempt_id=expected_attempt_id,
                expected_contract_fingerprint=expected_contract_fingerprint,
            )
            if attempt_id is None:
                raise RuntimeError("analysis retry result omitted successor identity")
            return projection, attempt_id
        await self._ensure_schema()
        async with self._writer_lock:
            return await self._with_busy_retry(
                lambda: self._retry_analysis_once(
                    command,
                    expected_attempt_id=expected_attempt_id,
                    expected_contract_fingerprint=expected_contract_fingerprint,
                )
            )

    async def fail_analysis_attempt(
        self,
        command: LifecycleCommand,
        *,
        attempt_id: str,
        lease_token: str | None,
        allow_expired_lease: bool = False,
    ) -> RunProjection:
        """Atomically close the authoritative attempt and move its Run to recovery."""
        if command.kind != "fail":
            raise LifecycleCommandConflict("analysis failure requires fail command")
        if self._writer is not None:
            projection, _ = await self._submit_lifecycle(
                "fail_analysis_attempt",
                command,
                attempt_id=attempt_id,
                lease_token=lease_token,
                allow_expired_lease=allow_expired_lease,
            )
            return projection
        await self._ensure_schema()
        async with self._writer_lock:
            return await self._with_busy_retry(
                lambda: self._fail_analysis_attempt_once(
                    command,
                    attempt_id=attempt_id,
                    lease_token=lease_token,
                    allow_expired_lease=allow_expired_lease,
                )
            )

    async def _submit_lifecycle(
        self,
        action: str,
        command: LifecycleCommand,
        **fields: Any,
    ) -> tuple[RunProjection, str | None]:
        from app.content_research.lifecycle.mutations import decode_run_projection

        assert self._writer is not None
        command_payload = {
            "command_id": command.command_id,
            "run_id": command.run_id,
            "expected_state": command.expected_state.value if command.expected_state else None,
            "expected_revision": command.expected_revision,
            "kind": command.kind,
            "payload": dict(command.payload),
        }
        try:
            result = await self._writer.submit(
                TypedMutation.create(
                    mutation_id=f"content_research_lifecycle:{command.command_id}",
                    mutation_kind="execute_content_research_lifecycle",
                    domain_payload={"action": action, "command": command_payload, **fields},
                    run_id=command.run_id,
                )
            )
        except DomainMutationRejectedError as exc:
            raise LifecycleCommandConflict(exc.safe_message) from None
        projection_payload = result.result_fields.get("projection")
        if not isinstance(projection_payload, dict):
            raise RuntimeError("invalid Content Research lifecycle result")
        attempt_id = result.result_fields.get("attempt_id")
        return (
            decode_run_projection(projection_payload),
            str(attempt_id) if attempt_id is not None else None,
        )

    async def _fail_analysis_attempt_once(
        self,
        command: LifecycleCommand,
        *,
        attempt_id: str,
        lease_token: str | None,
        allow_expired_lease: bool,
    ) -> RunProjection:
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            existing = await self._fetch_command(conn, command.command_id)
            request_fingerprint = _fingerprint(command)
            if existing is not None:
                if (
                    existing["run_id"] != command.run_id
                    or existing["command_kind"] != command.kind
                    or existing["request_fingerprint"] != request_fingerprint
                ):
                    raise LifecycleCommandConflict("command identity was reused with new input")
                projection = await self._load_in_transaction(conn, command.run_id)
                await conn.rollback()
                return projection
            async with conn.execute(
                "SELECT attempt.*, unit.workflow_run_id, "
                "run.effective_analysis_attempt_id "
                "FROM content_research_analysis_attempts AS attempt "
                "JOIN content_research_analysis_units AS unit "
                "ON unit.id=attempt.analysis_unit_id "
                "JOIN workflow_runs AS run ON run.run_id=unit.workflow_run_id "
                "WHERE attempt.id=? AND run.run_id=?",
                (attempt_id, command.run_id),
            ) as cursor:
                attempt = await cursor.fetchone()
            if attempt is None or attempt["effective_analysis_attempt_id"] != attempt_id:
                raise LifecycleCommandConflict("analysis failure attempt is stale")
            if str(attempt["state"]) != "running":
                raise LifecycleCommandConflict("analysis failure requires a running attempt")
            expires_at = (
                datetime.fromisoformat(str(attempt["lease_expires_at"]))
                if attempt["lease_expires_at"]
                else None
            )
            if allow_expired_lease:
                if expires_at is None or expires_at > datetime.now(timezone.utc):
                    raise LifecycleCommandConflict("analysis lease has not expired")
            elif not lease_token or attempt["lease_token"] != lease_token:
                raise LifecycleCommandConflict("analysis failure lease was fenced")

            result_revision = await self._advance(conn, command)
            now = _now()
            await conn.execute(
                "UPDATE content_research_analysis_attempts "
                "SET state='failed', terminal_at=?, lease_expires_at=NULL "
                "WHERE id=? AND state='running'",
                (now, attempt_id),
            )
            await conn.execute(
                "INSERT INTO content_research_lifecycle_commands "
                "(command_id, run_id, command_kind, request_fingerprint, "
                "result_revision, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    command.command_id,
                    command.run_id,
                    command.kind,
                    request_fingerprint,
                    result_revision,
                    now,
                ),
            )
            projection = await self._load_in_transaction(conn, command.run_id)
            await conn.commit()
            return projection
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def _retry_analysis_once(
        self,
        command: LifecycleCommand,
        *,
        expected_attempt_id: str,
        expected_contract_fingerprint: str,
    ) -> tuple[RunProjection, str]:
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            existing = await self._fetch_command(conn, command.command_id)
            request_fingerprint = _fingerprint(command)
            if existing is not None:
                if (
                    existing["run_id"] != command.run_id
                    or existing["command_kind"] != command.kind
                    or existing["request_fingerprint"] != request_fingerprint
                ):
                    raise LifecycleCommandConflict("command identity was reused with new input")
                async with conn.execute(
                    "SELECT id FROM content_research_analysis_attempts "
                    "WHERE successor_of_attempt_id=?",
                    (expected_attempt_id,),
                ) as cursor:
                    successor = await cursor.fetchone()
                if successor is None:
                    raise LifecycleCommandConflict(
                        "replayed analysis retry is missing its successor"
                    )
                projection = await self._load_in_transaction(conn, command.run_id)
                await conn.rollback()
                return projection, str(successor["id"])

            async with conn.execute(
                "SELECT attempt.*, unit.workflow_run_id, unit.contract_fingerprint "
                "FROM workflow_runs AS run "
                "JOIN content_research_analysis_attempts AS attempt "
                "ON attempt.id=run.effective_analysis_attempt_id "
                "JOIN content_research_analysis_units AS unit "
                "ON unit.id=attempt.analysis_unit_id "
                "JOIN content_research_analysis_jobs AS job "
                "ON job.analysis_unit_id=unit.id "
                "WHERE run.run_id=? AND unit.workflow_run_id=run.run_id",
                (command.run_id,),
            ) as cursor:
                attempt = await cursor.fetchone()
            if attempt is None:
                raise LifecycleCommandConflict("legacy run has no retryable analysis attempt")
            if str(attempt["id"]) != expected_attempt_id:
                raise LifecycleCommandConflict("analysis retry attempt is stale")
            if str(attempt["state"]) not in {"failed", "cancelled"}:
                raise LifecycleCommandConflict("analysis retry requires a failed predecessor")
            if str(attempt["contract_fingerprint"]) != expected_contract_fingerprint:
                raise LifecycleCommandConflict("analysis retry contract fingerprint changed")

            result_revision = await self._advance(conn, command)
            successor_no = int(attempt["attempt_no"]) + 1
            successor_id = (
                "ana_"
                + hashlib.sha256(
                    "\x1f".join((str(attempt["analysis_unit_id"]), str(successor_no))).encode(
                        "utf-8"
                    )
                ).hexdigest()[:24]
            )
            await conn.execute(
                "INSERT INTO content_research_analysis_attempts "
                "(id, analysis_unit_id, attempt_no, state, successor_of_attempt_id, "
                "created_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                (
                    successor_id,
                    str(attempt["analysis_unit_id"]),
                    successor_no,
                    expected_attempt_id,
                    _now(),
                ),
            )
            await conn.execute(
                "UPDATE workflow_runs SET effective_analysis_attempt_id=? WHERE run_id=?",
                (successor_id, command.run_id),
            )
            await conn.execute(
                "INSERT INTO content_research_lifecycle_commands "
                "(command_id, run_id, command_kind, request_fingerprint, "
                "result_revision, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    command.command_id,
                    command.run_id,
                    command.kind,
                    request_fingerprint,
                    result_revision,
                    _now(),
                ),
            )
            projection = await self._load_in_transaction(conn, command.run_id)
            await conn.commit()
            return projection, successor_id
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def _apply_once(self, command: LifecycleCommand) -> RunProjection:
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            existing = await self._fetch_command(conn, command.command_id)
            request_fingerprint = _fingerprint(command)
            if existing is not None:
                if (
                    existing["run_id"] != command.run_id
                    or existing["command_kind"] != command.kind
                    or existing["request_fingerprint"] != request_fingerprint
                ):
                    raise LifecycleCommandConflict("command identity was reused with new input")
                projection = await self._load_in_transaction(conn, command.run_id)
                await conn.rollback()
                return projection

            if command.kind == "submit_research_subject":
                await self._create_run(conn, command)
                result_revision = 1
            else:
                result_revision = await self._advance(conn, command)

            await conn.execute(
                """INSERT INTO content_research_lifecycle_commands
                   (command_id, run_id, command_kind, request_fingerprint,
                    result_revision, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    command.command_id,
                    command.run_id,
                    command.kind,
                    request_fingerprint,
                    result_revision,
                    _now(),
                ),
            )
            projection = await self._load_in_transaction(conn, command.run_id)
            await conn.commit()
            return projection
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def record(self, event: ExecutionEvent) -> RunProjection:
        current = await self.load(event.run_id)
        if event.attempt_id is not None or event.lease_token is not None:
            if current.execution_attempt_id is None:
                raise LifecycleCommandConflict(
                    "execution identity is not applicable to the current lifecycle state"
                )
            if event.attempt_id != current.execution_attempt_id:
                raise LifecycleCommandConflict("execution attempt was fenced")
        payload = {
            **dict(event.payload),
            "attempt_id": event.attempt_id,
            "lease_token": event.lease_token,
        }
        command = LifecycleCommand(
            command_id=(
                f"event:{event.run_id}:{event.kind}:{event.expected_revision}:"
                f"{event.attempt_id or '-'}:{event.lease_token or '-'}"
            ),
            run_id=event.run_id,
            expected_state=current.state,
            expected_revision=event.expected_revision,
            kind=event.kind,
            payload=payload,
        )
        return await self.apply(command)

    async def load(self, run_id: str) -> RunProjection:
        await self._ensure_schema()
        return await self._with_busy_retry(lambda: self._load_once(run_id))

    async def load_trace_snapshot(
        self,
        run_id: str,
        reader: Callable[
            [sqlite3.Connection, RunProjection, list[dict[str, Any]]],
            Awaitable[TraceSnapshotT],
        ],
    ) -> TraceSnapshotT:
        """Build one Trace projection inside one coordinator-owned read transaction."""
        return await self._with_busy_retry(lambda: self._load_trace_snapshot_once(run_id, reader))

    async def _load_trace_snapshot_once(
        self,
        run_id: str,
        reader: Callable[
            [sqlite3.Connection, RunProjection, list[dict[str, Any]]],
            Awaitable[TraceSnapshotT],
        ],
    ) -> TraceSnapshotT:
        connection = open_readonly_database(self._db_path, timeout=0.25)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=250")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        try:
            connection.execute("BEGIN")
            # Pin the WAL boundary before any projection component reads.
            connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is None
            ):
                raise LifecycleCommandConflict("Run does not exist")
            projection = self._load_sync_in_transaction(connection, run_id)
            transition_rows = connection.execute(
                """SELECT from_state, to_state, event, state_revision,
                          reason_code, attempt_id, created_at
                   FROM content_research_state_transitions
                   WHERE run_id=? ORDER BY state_revision ASC""",
                (run_id,),
            ).fetchall()
            result = await reader(
                connection,
                projection,
                [dict(row) for row in transition_rows],
            )
            connection.rollback()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _load_sync_in_transaction(connection: sqlite3.Connection, run_id: str) -> RunProjection:
        row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise LifecycleCommandConflict("Run does not exist")
        brief = connection.execute(
            """SELECT id FROM content_research_briefs
               WHERE workflow_run_id=? ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        scope = connection.execute(
            """SELECT id FROM content_research_scope_contracts
               WHERE workflow_run_id=? ORDER BY version DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        dispatch = connection.execute(
            "SELECT workflow_run_id, attempt_count "
            "FROM content_research_dispatch_jobs WHERE workflow_run_id=? LIMIT 1",
            (run_id,),
        ).fetchone()
        attempt = connection.execute(
            """SELECT a.execution_unit_id, a.attempt_no
               FROM content_research_scope_execution_attempts AS a
               JOIN content_research_scope_execution_units AS u
                 ON u.id=a.execution_unit_id
               WHERE u.workflow_run_id=?
               ORDER BY a.attempt_no DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        publication = connection.execute(
            """SELECT payload_json FROM workflow_artifacts
               WHERE run_id=? AND artifact_type='final_result'
                 AND artifact_version=? LIMIT 1""",
            (run_id, int(row["artifact_version"] or 0)),
        ).fetchone()
        return projection_from_row(
            row,
            brief_id=str(brief["id"]) if brief else None,
            scope_contract_id=str(scope["id"]) if scope else None,
            has_dispatch=dispatch is not None,
            dispatch_attempt_id=(
                f"{dispatch['workflow_run_id']}:{dispatch['attempt_count']}"
                if dispatch
                else None
            ),
            execution_attempt_id=(
                f"{attempt['execution_unit_id']}:{attempt['attempt_no']}" if attempt else None
            ),
            publication_id=_publication_id_from_artifact_payload(
                publication["payload_json"] if publication else None
            ),
        )

    async def _load_once(self, run_id: str) -> RunProjection:
        conn = await self._connect()
        try:
            return await self._load_in_transaction(conn, run_id)
        finally:
            await conn.close()

    async def load_historical_read_only(self, run_id: str) -> dict[str, Any]:
        """Decode a pre-lifecycle Run without granting mutation authority."""

        await self._ensure_schema()
        return await self._with_busy_retry(lambda: self._load_historical_read_only_once(run_id))

    async def _load_historical_read_only_once(self, run_id: str) -> dict[str, Any]:
        conn = await self._connect()
        try:
            row = await self._fetch_run(conn, run_id)
            if row is None:
                raise LifecycleCommandConflict("Run does not exist")
            if row["content_research_state"] is not None:
                raise LifecycleCommandConflict("Run uses the current lifecycle authority")
            return {
                "run_id": str(row["run_id"]),
                "thread_id": str(row["thread_id"]),
                "status": str(row["status"] or "unknown"),
                "phase": str(row["phase"] or "unknown"),
                "current_step": str(row["current_step"] or "unknown"),
                "started_at": row["started_at"],
                "updated_at": row["updated_at"],
                "read_only": True,
                "mutation_authority": None,
            }
        finally:
            await conn.close()

    def load_now(self, run_id: str) -> RunProjection:
        """Read a projection from an already bootstrapped runtime database."""

        for attempt_no in range(1, 4):
            try:
                with open_readonly_database(self._db_path, timeout=0.25) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA busy_timeout=250")
                    row = conn.execute(
                        "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
                    ).fetchone()
                    if row is None:
                        raise LifecycleCommandConflict("Run does not exist")
                    brief = conn.execute(
                        """SELECT id FROM content_research_briefs
                           WHERE workflow_run_id=? ORDER BY updated_at DESC, id DESC LIMIT 1""",
                        (run_id,),
                    ).fetchone()
                    scope = conn.execute(
                        """SELECT id FROM content_research_scope_contracts
                           WHERE workflow_run_id=? ORDER BY version DESC LIMIT 1""",
                        (run_id,),
                    ).fetchone()
                    dispatch = conn.execute(
                        "SELECT 1 FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
                        (run_id,),
                    ).fetchone()
                    attempt = conn.execute(
                        """SELECT a.execution_unit_id, a.attempt_no
                           FROM content_research_scope_execution_attempts AS a
                           JOIN content_research_scope_execution_units AS u
                             ON u.id=a.execution_unit_id
                           WHERE u.workflow_run_id=?
                           ORDER BY a.attempt_no DESC LIMIT 1""",
                        (run_id,),
                    ).fetchone()
                    publication = conn.execute(
                        """SELECT payload_json FROM workflow_artifacts
                           WHERE run_id=? AND artifact_type='final_result'
                             AND artifact_version=? LIMIT 1""",
                        (run_id, int(row["artifact_version"] or 0)),
                    ).fetchone()
                    publication_id = _publication_id_from_artifact_payload(
                        publication["payload_json"] if publication else None
                    )
                return projection_from_row(
                    row,
                    brief_id=str(brief["id"]) if brief else None,
                    scope_contract_id=str(scope["id"]) if scope else None,
                    has_dispatch=dispatch is not None,
                    execution_attempt_id=(
                        f"{attempt['execution_unit_id']}:{attempt['attempt_no']}"
                        if attempt
                        else None
                    ),
                    publication_id=publication_id,
                )
            except sqlite3.OperationalError as exc:
                if not self._is_sqlite_busy(exc):
                    raise
                if attempt_no == 3:
                    raise LifecyclePersistenceBusy(
                        "LOCAL_PERSISTENCE_BUSY after 3 attempts"
                    ) from exc
                time.sleep(0.05 * (2 ** (attempt_no - 1)))
        raise AssertionError("unreachable")

    async def list_transitions(self, run_id: str) -> list[dict[str, Any]]:
        await self._ensure_schema()
        return await self._with_busy_retry(lambda: self._list_transitions_once(run_id))

    async def _list_transitions_once(self, run_id: str) -> list[dict[str, Any]]:
        conn = await self._connect()
        try:
            async with conn.execute(
                """SELECT from_state, to_state, event, state_revision,
                          reason_code, attempt_id, created_at
                   FROM content_research_state_transitions
                   WHERE run_id=? ORDER BY state_revision ASC""",
                (run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await conn.close()

    async def reconcile_interrupted_presearch(self) -> list[RunProjection]:
        """Fence PreResearch work left in-flight by a previous process.

        Startup reconciliation never replays the LLM. It records a safe,
        retryable failure so the user can explicitly resume the same Run.
        """

        await self._ensure_schema()
        rows = await self._with_busy_retry(self._interrupted_presearch_rows)

        reconciled: list[RunProjection] = []
        for row in rows:
            request_payload = json.loads(str(row["request_payload_json"] or "{}"))
            previous_payload = json.loads(str(row["brief_payload_json"] or "{}"))
            run_id = str(row["run_id"])
            seed_text = str(
                request_payload.get("initial_request")
                or previous_payload.get("seed_text")
                or "本轮调研"
            ).strip()
            user_note = request_payload.get("user_note", previous_payload.get("user_note"))
            digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
            brief_id = str(row["brief_id"] or f"rb_recovery_{digest}")
            attempt_id = str(previous_payload.get("attempt_id") or f"att_recovery_{digest}")
            error = {
                "code": "PRESEARCH_PROCESS_INTERRUPTED",
                "stage": "presearch",
                "operation": "llm_presearch",
                "message": "Runtime 在轻量预检索完成前重启，请确认模型配置后重试。",
                "retryable": True,
                "recovery_action": "retry_presearch",
            }
            payload = {
                **previous_payload,
                "brief_id": brief_id,
                "schema_version": "content_research_brief_v1",
                "status": "failed",
                "brief_status": "failed",
                "subject": str(previous_payload.get("subject") or seed_text),
                "subject_confirmation": str(
                    previous_payload.get("subject_confirmation") or seed_text
                ),
                "competitors": list(previous_payload.get("competitors") or []),
                "competitor_tags": list(previous_payload.get("competitor_tags") or []),
                "directions": list(previous_payload.get("directions") or ["product_marketing"]),
                "research_directions": list(
                    previous_payload.get("research_directions") or ["product_marketing"]
                ),
                "attempt_id": attempt_id,
                "seed_text": seed_text,
                "user_note": user_note,
                "workspace_id": str(
                    request_payload.get("workspace_id")
                    or previous_payload.get("workspace_id")
                    or "default"
                ),
                "user_id": str(row["user_id"] or "default"),
                "error_code": error["code"],
                "error_message": error["message"],
                "recoverable": True,
                "timeout_status": "none",
                "fallback_used": False,
                "error": error,
            }
            try:
                reconciled.append(
                    await self.apply(
                        LifecycleCommand(
                            command_id=f"startup-reconcile:{run_id}:{row['state_revision']}",
                            run_id=run_id,
                            expected_state=ContentResearchState.PRESEARCH_RUNNING,
                            expected_revision=int(row["state_revision"]),
                            kind="fail",
                            payload=payload,
                        )
                    )
                )
            except LifecycleCommandConflict:
                current = await self.load(run_id)
                if current.state is ContentResearchState.PRESEARCH_RUNNING:
                    raise
        return reconciled

    async def _interrupted_presearch_rows(self) -> list[aiosqlite.Row]:
        conn = await self._connect()
        try:
            async with conn.execute(
                """SELECT r.run_id, r.thread_id, r.user_id, r.state_revision,
                          b.id AS brief_id, b.payload_json AS brief_payload_json,
                          e.payload_json AS request_payload_json
                   FROM workflow_runs AS r
                   LEFT JOIN content_research_briefs AS b
                     ON b.id=(
                       SELECT latest.id FROM content_research_briefs AS latest
                       WHERE latest.workflow_run_id=r.run_id
                       ORDER BY latest.updated_at DESC, latest.id DESC LIMIT 1
                     )
                   LEFT JOIN workflow_events AS e
                     ON e.event_id=(
                       SELECT started.event_id FROM workflow_events AS started
                       WHERE started.run_id=r.run_id AND started.event_type='run_started'
                       ORDER BY started.event_id ASC LIMIT 1
                     )
                   WHERE r.content_research_state=?
                   ORDER BY r.started_at ASC, r.run_id ASC""",
                (ContentResearchState.PRESEARCH_RUNNING.value,),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return list(rows)

    async def _create_run(
        self,
        conn: aiosqlite.Connection,
        command: LifecycleCommand,
    ) -> None:
        if command.expected_state is not None or command.expected_revision != 0:
            raise LifecycleCommandConflict("new Run must target revision zero")
        thread_id = str(command.payload.get("thread_id") or "")
        user_id = str(command.payload.get("user_id") or "")
        seed_text = str(command.payload.get("seed_text") or "").strip()
        if not thread_id or not user_id or not seed_text:
            raise ValueError("thread_id, user_id, and seed_text are required")
        existing = await self._fetch_run(conn, command.run_id)
        if existing is not None:
            raise LifecycleCommandConflict("Run already exists without this command identity")
        now = _now()
        await conn.execute(
            """INSERT INTO workflow_runs
               (run_id, thread_id, user_id, status, phase, current_step,
                interrupt_policy, started_at, created_at, updated_at,
                content_research_state, state_revision, state_entered_at,
                lifecycle_schema_version)
               VALUES (?, ?, ?, 'running', 'intake', 'presearch',
                       'safe_boundary', ?, ?, ?, ?, 1, ?, ?)""",
            (
                command.run_id,
                thread_id,
                user_id,
                now,
                now,
                now,
                ContentResearchState.PRESEARCH_RUNNING.value,
                now,
                "content_research_lifecycle_v1",
            ),
        )
        cursor = await conn.execute(
            """UPDATE creator_threads
               SET active_run_id=?, updated_at=? WHERE id=?""",
            (command.run_id, now, thread_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Creator thread does not exist")
        await conn.execute(
            """INSERT INTO content_research_state_transitions
               (run_id, thread_id, from_state, to_state, event,
                state_revision, created_at)
               VALUES (?, ?, NULL, ?, 'submit_research_subject', 1, ?)""",
            (command.run_id, thread_id, ContentResearchState.PRESEARCH_RUNNING.value, now),
        )
        for step_name, phase, status in (
            ("presearch", "intake", "running"),
            ("brief_confirm", "intake", "pending"),
            ("scope_confirm", "intake", "pending"),
            ("formal_research", "retrieval", "pending"),
            ("coverage", "retrieval", "pending"),
            ("report", "finalization", "pending"),
        ):
            await conn.execute(
                """INSERT INTO workflow_steps
                   (step_id, run_id, step_name, phase, status, attempt_count,
                    max_attempts, started_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 3, ?, ?, ?)""",
                (
                    f"step_{uuid.uuid4().hex}",
                    command.run_id,
                    step_name,
                    phase,
                    status,
                    1 if status == "running" else 0,
                    now if status == "running" else None,
                    now,
                    now,
                ),
            )
        await conn.execute(
            """INSERT INTO workflow_events
               (run_id, thread_id, event_type, event_level, payload_json, created_at)
               VALUES (?, ?, 'run_started', 'info', ?, ?)""",
            (
                command.run_id,
                thread_id,
                _canonical_json(
                    {
                        "initial_request": seed_text,
                        "user_note": command.payload.get("user_note"),
                        "workspace_id": command.payload.get("workspace_id"),
                        "user_id": user_id,
                    }
                ),
                now,
            ),
        )

    async def _advance(
        self,
        conn: aiosqlite.Connection,
        command: LifecycleCommand,
    ) -> int:
        row = await self._fetch_run(conn, command.run_id)
        if row is None:
            raise LifecycleCommandConflict("Run does not exist")
        current_state = ContentResearchState(str(row["content_research_state"]))
        current_revision = int(row["state_revision"] or 0)
        if command.expected_state is None or command.expected_state is not current_state:
            raise LifecycleCommandConflict("expected state does not match current state")
        if command.expected_revision != current_revision:
            raise LifecycleCommandConflict("expected revision does not match current revision")
        if command.kind in _RECOVERY_COMMANDS:
            current = await self._load_in_transaction(conn, command.run_id)
            plan = current.recovery_plan
            if (
                plan is None
                or plan.action != command.kind
                or command.payload.get("recovery_plan_id") != plan.recovery_plan_id
                or command.payload.get("plan_fingerprint") != plan.plan_fingerprint
            ):
                raise LifecycleCommandConflict("recovery plan is unavailable or stale")
        if command.kind == "cancel":
            async with conn.execute(
                "SELECT 1 FROM content_research_report_publications "
                "WHERE workflow_run_id=? LIMIT 1",
                (command.run_id,),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    raise LifecycleCommandConflict(
                        "report publication already committed; cancellation lost the race"
                    )
        try:
            decision = transition(
                current_state=current_state,
                current_revision=current_revision,
                event=command.kind,
            )
        except LifecycleTransitionError as exc:
            raise LifecycleCommandConflict(str(exc)) from exc

        brief_id = None
        if command.kind == "presearch_completed":
            brief_id = await self._persist_presearch_brief(conn, row, command.payload)
        elif command.kind == "confirm_brief":
            brief_id = await self._persist_confirmed_brief_and_scope_draft(
                conn, row, command.payload
            )
        elif command.kind == "replace_scope_draft":
            await self._persist_scope_draft_replacement(conn, row, command.payload)
        elif command.kind == "confirm_scope":
            await self._persist_scope_confirmation_and_dispatch(
                conn, row, command.payload, command_id=command.command_id
            )
        elif command.kind == "fail" and command.payload.get("brief_id"):
            brief_id = await self._persist_presearch_brief(conn, row, command.payload)

        error_payload = dict(command.payload.get("error") or {})
        if command.kind == "fail" and not error_payload.get("attempt_id"):
            attempt_id = str(command.payload.get("attempt_id") or "")
            if attempt_id:
                error_payload["attempt_id"] = attempt_id
        reason_code = str(error_payload.get("code") or "") or None
        if command.kind == "cancel":
            reason_code = "user_cancelled"
        now = _now()
        if decision.to_state in {
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
            ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
            ContentResearchState.COVERAGE_DECISION_REQUIRED,
            ContentResearchState.RECOVERY_REQUIRED,
        }:
            status = "waiting_user"
        elif decision.to_state is ContentResearchState.CANCELLED_OR_FAILED:
            status = "failed"
        elif decision.to_state is ContentResearchState.REPORT_READY:
            status = "succeeded"
        else:
            status = "running"
        current_step = (
            "brief_confirm"
            if decision.to_state is ContentResearchState.BRIEF_CONFIRMATION_REQUIRED
            else "scope_confirm"
            if decision.to_state is ContentResearchState.SCOPE_CONFIRMATION_REQUIRED
            else "formal_research"
            if decision.to_state
            in {
                ContentResearchState.RETRIEVAL_QUEUED,
                ContentResearchState.RETRIEVAL_RUNNING,
                ContentResearchState.COVERAGE_EVALUATING,
            }
            else "coverage"
            if decision.to_state is ContentResearchState.COVERAGE_DECISION_REQUIRED
            else "report"
            if decision.to_state
            in {
                ContentResearchState.REPORT_COMPOSING,
                ContentResearchState.REPORT_READY,
            }
            else row["current_step"]
        )
        phase = (
            "retrieval"
            if decision.to_state
            in {
                ContentResearchState.RETRIEVAL_QUEUED,
                ContentResearchState.RETRIEVAL_RUNNING,
                ContentResearchState.COVERAGE_EVALUATING,
                ContentResearchState.COVERAGE_DECISION_REQUIRED,
            }
            else "finalization"
            if decision.to_state
            in {
                ContentResearchState.REPORT_COMPOSING,
                ContentResearchState.REPORT_READY,
            }
            else str(row["phase"] or "intake")
        )
        await conn.execute(
            """UPDATE workflow_runs
               SET content_research_state=?, state_revision=?, state_entered_at=?,
                   status=?, phase=?, current_step=?, error_code=?, error_message=?,
                   lifecycle_error_json=?, updated_at=?
               WHERE run_id=?""",
            (
                decision.to_state.value,
                decision.next_revision,
                now,
                status,
                phase,
                current_step,
                reason_code,
                str(error_payload.get("message") or "") or None,
                _canonical_json(error_payload) if error_payload else None,
                now,
                command.run_id,
            ),
        )
        if command.kind == "presearch_completed":
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='succeeded', completed_at=?, updated_at=?
                   WHERE run_id=? AND step_name='presearch'""",
                (now, now, command.run_id),
            )
        elif command.kind == "confirm_brief":
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='succeeded', completed_at=?, updated_at=?
                   WHERE run_id=? AND step_name='brief_confirm'""",
                (now, now, command.run_id),
            )
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='waiting_user', attempt_count=CASE WHEN attempt_count=0 THEN 1 ELSE attempt_count END,
                       started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE run_id=? AND step_name='scope_confirm'""",
                (now, now, command.run_id),
            )
        elif command.kind == "confirm_scope":
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='succeeded', completed_at=?, updated_at=?
                   WHERE run_id=? AND step_name='scope_confirm'""",
                (now, now, command.run_id),
            )
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='running', attempt_count=CASE WHEN attempt_count=0 THEN 1 ELSE attempt_count END,
                       started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE run_id=? AND step_name='formal_research'""",
                (now, now, command.run_id),
            )
        elif command.kind == "worker_claimed":
            await conn.execute(
                """UPDATE workflow_steps SET status='running', updated_at=?
                   WHERE run_id=? AND step_name='formal_research'""",
                (now, command.run_id),
            )
        elif command.kind == "retrieval_completed":
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='running', attempt_count=CASE WHEN attempt_count=0 THEN 1 ELSE attempt_count END,
                       started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE run_id=? AND step_name='coverage'""",
                (now, now, command.run_id),
            )
        elif command.kind in {"coverage_satisfied", "coverage_insufficient"}:
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='succeeded', completed_at=?, updated_at=?
                   WHERE run_id=? AND step_name='coverage'""",
                (now, now, command.run_id),
            )
            if command.kind == "coverage_satisfied":
                await conn.execute(
                    """UPDATE workflow_steps
                       SET status='running', attempt_count=CASE WHEN attempt_count=0 THEN 1 ELSE attempt_count END,
                           started_at=COALESCE(started_at, ?), updated_at=?
                       WHERE run_id=? AND step_name='report'""",
                    (now, now, command.run_id),
                )
        elif command.kind == "report_published":
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='succeeded', completed_at=?, updated_at=?
                   WHERE run_id=? AND step_name='report'""",
                (now, now, command.run_id),
            )
        elif command.kind in {"revise_subject", "retry_presearch"}:
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='running', attempt_count=attempt_count+1,
                       started_at=?, completed_at=NULL, error_code=NULL,
                       error_message=NULL, updated_at=?
                   WHERE run_id=? AND step_name='presearch'""",
                (now, now, command.run_id),
            )
        elif command.kind in {"retry_analysis", "retry_report"}:
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='running', attempt_count=attempt_count+1,
                       started_at=COALESCE(started_at, ?), completed_at=NULL,
                       error_code=NULL, error_message=NULL, updated_at=?
                   WHERE run_id=? AND step_name='report'""",
                (now, now, command.run_id),
            )
        elif command.kind == "fail":
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='failed', completed_at=?, error_code=?, error_message=?,
                       updated_at=?
                   WHERE run_id=? AND step_name=?""",
                (
                    now,
                    reason_code,
                    str(error_payload.get("message") or "") or None,
                    now,
                    command.run_id,
                    str(row["current_step"] or "presearch"),
                ),
            )
        elif command.kind == "cancel":
            await conn.execute(
                """UPDATE content_research_dispatch_jobs
                   SET status='failed', lease_expires_at=NULL, lease_owner=NULL,
                       lease_token=NULL, last_error='user_cancelled', updated_at=?
                   WHERE workflow_run_id=? AND status IN ('queued', 'running')""",
                (now, command.run_id),
            )
            await conn.execute(
                """UPDATE content_research_scope_execution_attempts
                   SET state='cancelled', lease_expires_at=NULL
                   WHERE execution_unit_id IN (
                       SELECT id FROM content_research_scope_execution_units
                       WHERE workflow_run_id=?
                   ) AND state IN ('pending', 'running')""",
                (command.run_id,),
            )
            await conn.execute(
                """UPDATE content_research_scope_execution_units
                   SET state='cancelled' WHERE workflow_run_id=?
                   AND state NOT IN ('completed', 'failed', 'outcome_unknown', 'cancelled')""",
                (command.run_id,),
            )
            await conn.execute(
                """UPDATE content_research_analysis_attempts
                   SET state='cancelled', terminal_at=?, lease_expires_at=NULL
                   WHERE analysis_unit_id IN (
                       SELECT id FROM content_research_analysis_units
                       WHERE workflow_run_id=?
                   ) AND state IN ('queued', 'running')""",
                (now, command.run_id),
            )
            await conn.execute(
                """UPDATE workflow_steps
                   SET status='cancelled', completed_at=COALESCE(completed_at, ?),
                       error_code='CANCELLED_BY_USER',
                       error_message='Content Research Run cancelled by user',
                       updated_at=?
                   WHERE run_id=? AND status IN ('pending', 'running', 'retrying')""",
                (now, now, command.run_id),
            )
        await conn.execute(
            """INSERT INTO content_research_state_transitions
               (run_id, thread_id, from_state, to_state, event,
                state_revision, reason_code, error_json, attempt_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                command.run_id,
                row["thread_id"],
                decision.from_state.value,
                decision.to_state.value,
                decision.event,
                decision.next_revision,
                reason_code,
                _canonical_json(error_payload) if error_payload else None,
                str(command.payload.get("attempt_id") or "") or None,
                now,
            ),
        )
        if brief_id is not None or command.kind in {"replace_scope_draft", "confirm_scope"}:
            await conn.execute(
                "UPDATE workflow_runs SET artifact_version=artifact_version+1 WHERE run_id=?",
                (command.run_id,),
            )
        return decision.next_revision

    async def _persist_scope_confirmation_and_dispatch(
        self,
        conn: aiosqlite.Connection,
        run_row: aiosqlite.Row,
        payload: Any,
        *,
        command_id: str,
    ) -> None:
        """Freeze the latest draft and create its complete executable request atomically."""

        run_id = str(run_row["run_id"])
        draft_id = str(payload.get("scope_draft_id") or "")
        cursor = await conn.execute(
            """SELECT * FROM content_research_scope_drafts
               WHERE workflow_run_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
            (run_id,),
        )
        draft_row = await cursor.fetchone()
        if draft_row is None or str(draft_row["id"]) != draft_id:
            raise LifecycleCommandConflict("Scope confirmation requires the latest draft")

        brief_cursor = await conn.execute(
            """SELECT * FROM content_research_briefs
               WHERE workflow_run_id=? ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (run_id,),
        )
        brief_row = await brief_cursor.fetchone()
        if brief_row is None:
            raise LifecycleCommandConflict("Scope confirmation requires a current Brief")
        brief_payload = json.loads(str(brief_row["payload_json"]))
        if str(draft_row["structure_hash"]) != str(
            brief_payload.get("subject_structure_hash") or ""
        ):
            raise LifecycleCommandConflict("Scope draft does not match the current Brief")

        plan_cursor = await conn.execute(
            "SELECT * FROM content_research_plans WHERE id=? AND workflow_run_id=?",
            (draft_row["research_plan_id"], run_id),
        )
        plan_row = await plan_cursor.fetchone()
        if plan_row is None:
            raise LifecycleCommandConflict("Scope confirmation requires its current Plan")
        plan_payload = json.loads(str(plan_row["payload_json"]))
        selected_direction_ids = tuple(
            str(item) for item in plan_payload.get("direction_ids") or () if str(item)
        )
        if not selected_direction_ids or not set(selected_direction_ids).issubset(
            DIRECTION_CATALOG_V1
        ):
            raise LifecycleCommandConflict("Scope Plan has no executable Lite directions")

        constraints = tuple(
            ScopeConstraint(
                str(item["id"]),
                str(item["label"]),
                str(item["value"]),
                str(item["mode"]),
                tuple(item.get("allowed_aliases") or ()),
            )
            for item in json.loads(str(draft_row["constraints_json"]))
        )
        draft_groups = tuple(
            ScopeQueryGroupInput(
                suggested_query=str(item["suggested_query"]),
                final_query=str(item["final_query"]),
                targeted_required_terms=tuple(item.get("targeted_required_terms") or ()),
                origin=item.get("origin"),
            )
            for item in json.loads(str(draft_row["query_groups_json"]))
        )
        contract = build_scope_contract(
            workflow_run_id=run_id,
            research_plan_id=str(plan_row["id"]),
            version=1,
            schema_version=str(draft_row["schema_version"]),
            constraints=constraints,
            query_groups=draft_groups,
        )
        now = _now()
        await conn.execute(
            """INSERT INTO content_research_scope_contracts
               (id, workflow_run_id, research_plan_id, version, schema_version,
                constraints_json, query_groups_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract.id,
                run_id,
                contract.research_plan_id,
                contract.version,
                contract.schema_version,
                _canonical_json([item.__dict__ for item in contract.constraints]),
                _canonical_json([item.__dict__ for item in contract.query_groups]),
                now,
            ),
        )
        audit_id = f"sae_{hashlib.sha256(command_id.encode()).hexdigest()[:24]}"
        audit_payload = {
            "schema_version": "content_research_scope_audit_event_v1",
            "scope_draft_id": draft_id,
            "structure_hash": str(draft_row["structure_hash"]),
            "scope_contract_id": contract.id,
            "scope_contract_version": contract.version,
            "query_groups": [item.__dict__ for item in contract.query_groups],
            "queries": [
                {
                    "query_group_id": item.id,
                    "suggested_query": item.suggested_query,
                    "final_query": item.final_query,
                    "changed": item.origin == "user_edited",
                }
                for item in contract.query_groups
            ],
        }
        await conn.execute(
            """INSERT INTO content_research_scope_audit_events
               (id, workflow_run_id, scope_contract_id, scope_contract_version,
                event_name, payload_json, metadata_json, created_at)
               VALUES (?, ?, ?, ?, 'scope_confirmed', ?, '{}', ?)""",
            (audit_id, run_id, contract.id, contract.version, _canonical_json(audit_payload), now),
        )
        await conn.execute(
            """INSERT INTO content_research_scope_draft_confirmations
               (scope_draft_id, workflow_run_id, scope_contract_id, created_at)
               VALUES (?, ?, ?, ?)""",
            (draft_id, run_id, contract.id, now),
        )

        definitions = {item.id: item for item in ResearchDirectionRegistry().list_directions()}
        as_of = datetime.now(timezone.utc)
        window = {
            "start_at": (as_of - timedelta(days=365)).isoformat(),
            "end_at": as_of.isoformat(),
        }
        query_groups_by_direction = {
            direction_id: tuple(
                {
                    "id": group.id,
                    "direction_id": direction_id,
                    "normalized_query": group.final_query,
                    "priority": priority,
                    "sort": "likes",
                    "time_window": window,
                    "candidate_cap": 20,
                    "roles": [group.execution_role],
                    "activation": "primary",
                    "normalized_identity": hashlib.sha256(
                        _canonical_json(
                            {"scope_contract_id": contract.id, "group_id": group.id}
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                for priority, group in enumerate(contract.query_groups)
            )
            for direction_id in selected_direction_ids
        }
        snapshot, policies, direction_contracts = build_default_snapshot(
            snapshot_id=f"rps_{hashlib.sha256(command_id.encode()).hexdigest()[:24]}",
            workflow_run_id=run_id,
            brief_id=str(brief_row["id"]),
            plan_id=str(plan_row["id"]),
            run_as_of_at=as_of,
            direction_ids=selected_direction_ids,
            direction_catalog=DIRECTION_CATALOG_V1,
            report_compose_mode="template_only",
            provider_capabilities=dict(payload.get("provider_capabilities") or {}),
            confirmed_subject=str(
                draft_row["core_object"]
                or brief_payload.get("seed_text")
                or brief_payload.get("subject_confirmation")
            ),
            query_groups_by_direction=query_groups_by_direction,
            subject_structure=dict(brief_payload.get("subject_structure") or {}),
            subject_structure_hash=str(brief_payload.get("subject_structure_hash") or ""),
            primary_marketing_goal=(
                "content_seeding" if "product_marketing" in selected_direction_ids else None
            ),
        )
        await conn.execute(
            "INSERT INTO content_research_run_policy_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.id,
                snapshot.workflow_run_id,
                snapshot.research_brief_id,
                snapshot.research_plan_id,
                snapshot.schema_version,
                _canonical_json(snapshot.effective_policy),
                snapshot.effective_policy_hash,
                snapshot.run_as_of_at.isoformat(),
                _canonical_json(snapshot.base_policy_ids_and_versions),
                _canonical_json(snapshot.requested_overrides),
                _canonical_json(snapshot.validation_result),
                snapshot.created_at.isoformat(),
                _canonical_json(snapshot.metadata),
            ),
        )
        for policy in policies:
            await conn.execute(
                "INSERT INTO content_research_sample_policies VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    policy.id,
                    policy.schema_version,
                    policy.direction_id,
                    policy.minimum_samples,
                    policy.minimum_independent_authors,
                    policy.author_cap,
                    _canonical_json(policy.metadata),
                ),
            )
        for direction_contract in direction_contracts:
            await conn.execute(
                "INSERT INTO content_research_direction_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    direction_contract.id,
                    direction_contract.snapshot_id,
                    direction_contract.direction_id,
                    direction_contract.schema_version,
                    direction_contract.sample_policy_id,
                    _canonical_json(list(direction_contract.required_note_fields)),
                    _canonical_json(list(direction_contract.optional_note_fields)),
                    _canonical_json(list(direction_contract.required_comment_fields)),
                    _canonical_json(list(direction_contract.claim_rules)),
                    direction_contract.analysis_schema_version,
                    direction_contract.resume_contract_version,
                    _canonical_json(direction_contract.metadata),
                ),
            )

        formal_cursor = await conn.execute(
            "SELECT step_id FROM workflow_steps WHERE run_id=? AND step_name='formal_research'",
            (run_id,),
        )
        formal_step = await formal_cursor.fetchone()
        if formal_step is None:
            raise LifecycleCommandConflict("formal research step is missing")
        subject_structure = dict(brief_payload.get("subject_structure") or {})
        competitors = list(brief_payload.get("selected_competitors") or [])
        custom_competitor = str(brief_payload.get("custom_competitor_input") or "").strip()
        if custom_competitor:
            competitors.append(custom_competitor)
        task_ids: list[str] = []
        for index, direction_id in enumerate(selected_direction_ids):
            definition = definitions[direction_id]
            child_id = (
                f"child_{hashlib.sha256(f'{command_id}:{direction_id}'.encode()).hexdigest()[:24]}"
            )
            task_id = (
                f"sat_{hashlib.sha256(f'{command_id}:{direction_id}'.encode()).hexdigest()[:24]}"
            )
            task_ids.append(task_id)
            await conn.execute(
                """INSERT INTO workflow_child_tasks
                   (child_task_id, run_id, step_id, task_type, slot_index, status,
                    attempt_count, max_attempts, checkpoint_json, timing_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', 0, 3, ?, '{}', ?, ?)""",
                (
                    child_id,
                    run_id,
                    formal_step["step_id"],
                    definition.task_type,
                    index + 1,
                    _canonical_json({"direction_id": direction_id}),
                    now,
                    now,
                ),
            )
            task_payload = {
                "schema_version": "content_research_subagent_task_v1",
                "workflow_run_id": run_id,
                "research_brief_id": str(brief_row["id"]),
                "research_plan_id": str(plan_row["id"]),
                "research_direction_id": direction_id,
                "agent_name": definition.agent_name,
                "agent_version": "p0_spec_v1",
                "task_type": definition.task_type,
                "input_payload": {
                    "schema_version": "content_research_subagent_input_v1",
                    "confirmed_subject": str(draft_row["core_object"]),
                    "subject_structure": subject_structure,
                    "subject_structure_hash": str(
                        brief_payload.get("subject_structure_hash") or ""
                    ),
                    "competitors": competitors,
                    "custom_research_question": "",
                    "direction": {
                        "id": direction_id,
                        "label": definition.label,
                        "direction_type": definition.direction_type,
                        "questions": definition.default_questions,
                        "source_scope": definition.source_scope,
                    },
                },
                "expected_output_schema": {
                    "schema_version": "content_research_subagent_output_schema_v1",
                    "required": ["finding", "evidence_refs", "missing_evidence"],
                },
                "status": "queued",
                "sequence_no": index + 1,
                "workflow_child_task_id": child_id,
            }
            await conn.execute(
                """INSERT INTO content_research_subagent_tasks
                   (id, workflow_run_id, thread_id, schema_version, status, plan_id,
                    direction_id, created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, 'content_research_subagent_task_v1', 'queued', ?, ?, ?, ?, ?, '{}')""",
                (
                    task_id,
                    run_id,
                    run_row["thread_id"],
                    plan_row["id"],
                    direction_id,
                    now,
                    now,
                    _canonical_json(task_payload),
                ),
            )

        brief_payload.update(
            {
                "status": "ready",
                "confirmed_subject": str(draft_row["core_object"]),
                "requested_direction_ids": list(selected_direction_ids),
                "selected_directions": list(selected_direction_ids),
            }
        )
        await conn.execute(
            "UPDATE content_research_briefs SET status='ready', payload_json=?, updated_at=? WHERE id=?",
            (_canonical_json(brief_payload), now, brief_row["id"]),
        )
        plan_payload.update(
            {
                "selected_directions": list(selected_direction_ids),
                "subagent_task_ids": task_ids,
                "scope_contract_id": contract.id,
            }
        )
        await conn.execute(
            "UPDATE content_research_plans SET status='ready', payload_json=?, updated_at=? WHERE id=?",
            (_canonical_json(plan_payload), now, plan_row["id"]),
        )
        await conn.execute(
            """INSERT INTO content_research_dispatch_jobs
               (workflow_run_id, provider, source_kind, limit_per_specialist,
                status, attempt_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'queued', 0, ?, ?)""",
            (
                run_id,
                str(payload.get("provider") or "xiaohongshu"),
                str(payload.get("source_kind") or "search_result"),
                int(payload.get("limit") or 20),
                now,
                now,
            ),
        )

    async def _persist_confirmed_brief_and_scope_draft(
        self,
        conn: aiosqlite.Connection,
        run_row: aiosqlite.Row,
        payload: Any,
    ) -> str:
        brief_id = str(payload.get("brief_id") or "")
        plan = dict(payload.get("plan") or {})
        directions = list(payload.get("directions") or [])
        draft = dict(payload.get("scope_draft") or {})
        if not brief_id or not plan.get("id") or not draft.get("id") or not directions:
            raise ValueError("confirm_brief requires Brief, Plan, directions, and Scope Draft")
        if str(draft.get("workflow_run_id") or "") != str(run_row["run_id"]) or str(
            draft.get("research_plan_id") or ""
        ) != str(plan["id"]):
            raise LifecycleCommandConflict("Scope Draft lineage does not match this Run and Plan")
        cursor = await conn.execute(
            "SELECT * FROM content_research_briefs WHERE id=? AND workflow_run_id=?",
            (brief_id, run_row["run_id"]),
        )
        brief = await cursor.fetchone()
        if brief is None:
            raise LifecycleCommandConflict("Brief identity does not belong to this Run")
        now = _now()
        confirmed_payload = json.loads(str(brief["payload_json"]))
        confirmed_payload.update(dict(payload.get("brief_confirmation") or {}))
        confirmed_payload["confirmed_at"] = now
        await conn.execute(
            """UPDATE content_research_briefs
               SET status='confirmed', payload_json=?, updated_at=? WHERE id=?""",
            (_canonical_json(confirmed_payload), now, brief_id),
        )
        await conn.execute(
            """INSERT INTO content_research_plans
               (id, brief_id, workflow_run_id, thread_id, schema_version, status,
                created_at, updated_at, payload_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, '{}')""",
            (
                plan["id"],
                brief_id,
                run_row["run_id"],
                run_row["thread_id"],
                str(plan.get("schema_version") or "content_research_plan_v2"),
                now,
                now,
                _canonical_json(dict(plan.get("payload") or {})),
            ),
        )
        for priority, direction in enumerate(directions):
            direction = dict(direction)
            await conn.execute(
                """INSERT INTO content_research_directions
                   (id, plan_id, workflow_run_id, thread_id, schema_version, status,
                    priority, created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, '{}')""",
                (
                    direction["id"],
                    plan["id"],
                    run_row["run_id"],
                    run_row["thread_id"],
                    str(direction.get("schema_version") or "content_research_direction_v2"),
                    priority,
                    now,
                    now,
                    _canonical_json(dict(direction.get("payload") or {})),
                ),
            )
        await self._insert_scope_draft(conn, draft, now=now)
        return brief_id

    async def _persist_scope_draft_replacement(
        self,
        conn: aiosqlite.Connection,
        run_row: aiosqlite.Row,
        payload: Any,
    ) -> None:
        replaces_id = str(payload.get("replaces_scope_draft_id") or "")
        draft = dict(payload.get("scope_draft") or {})
        cursor = await conn.execute(
            """SELECT id FROM content_research_scope_drafts
               WHERE workflow_run_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
            (run_row["run_id"],),
        )
        latest = await cursor.fetchone()
        if latest is None or str(latest["id"]) != replaces_id:
            raise LifecycleCommandConflict("stale Scope Draft replacement")
        if str(draft.get("workflow_run_id") or "") != str(run_row["run_id"]):
            raise LifecycleCommandConflict("Scope Draft lineage does not match this Run")
        await self._insert_scope_draft(conn, draft, now=_now())

    async def _insert_scope_draft(
        self,
        conn: aiosqlite.Connection,
        draft: dict[str, Any],
        *,
        now: str,
    ) -> None:
        required = (
            "id",
            "workflow_run_id",
            "research_plan_id",
            "structure_hash",
            "constraints",
            "query_groups",
        )
        if any(not draft.get(key) for key in required):
            raise ValueError("Scope Draft payload is incomplete")
        await conn.execute(
            """INSERT INTO content_research_scope_drafts
               (id, workflow_run_id, research_plan_id, structure_hash, constraints_json,
                query_groups_json, created_at, schema_version, core_object,
                product_experience_aspect, context_audience_aspect)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft["id"],
                draft["workflow_run_id"],
                draft["research_plan_id"],
                draft["structure_hash"],
                _canonical_json(draft["constraints"]),
                _canonical_json(draft["query_groups"]),
                now,
                str(draft.get("schema_version") or "content_research_scope_contract_v2"),
                draft.get("core_object"),
                draft.get("product_experience_aspect"),
                draft.get("context_audience_aspect"),
            ),
        )
        audit_id = str(draft.get("audit_event_id") or "")
        if not audit_id:
            raise ValueError("Scope Draft audit identity is required")
        await conn.execute(
            """INSERT INTO content_research_scope_draft_audit_events
               (id, workflow_run_id, scope_draft_id, event_name, payload_json, created_at)
               VALUES (?, ?, ?, 'scope_suggested', ?, ?)""",
            (
                audit_id,
                draft["workflow_run_id"],
                draft["id"],
                _canonical_json(
                    {
                        "schema_version": "content_research_scope_audit_event_v1",
                        "scope_draft_id": draft["id"],
                        "replaces_scope_draft_id": draft.get("replaces_scope_draft_id"),
                        "query_groups": draft["query_groups"],
                    }
                ),
                now,
            ),
        )

    async def _persist_presearch_brief(
        self,
        conn: aiosqlite.Connection,
        run_row: aiosqlite.Row,
        payload: Any,
    ) -> str:
        brief_id = str(payload.get("brief_id") or "")
        schema_version = str(payload.get("schema_version") or "")
        attempt_id = str(payload.get("attempt_id") or "")
        subject = str(payload.get("subject") or "").strip()
        directions = list(payload.get("directions") or [])
        if not brief_id or not schema_version or not attempt_id or not subject or not directions:
            raise ValueError("completed PreResearch requires a complete Brief payload")
        now = _now()
        brief_payload = dict(payload)
        brief_payload["schema_version"] = schema_version
        existing = await conn.execute(
            "SELECT workflow_run_id, thread_id FROM content_research_briefs WHERE id=?",
            (brief_id,),
        )
        existing_row = await existing.fetchone()
        if existing_row is not None and (
            str(existing_row["workflow_run_id"]) != str(run_row["run_id"])
            or str(existing_row["thread_id"]) != str(run_row["thread_id"])
        ):
            raise LifecycleCommandConflict("Brief identity belongs to another Run")
        await conn.execute(
            """INSERT INTO content_research_briefs
               (id, workflow_run_id, thread_id, schema_version, status,
                created_at, updated_at, payload_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
               ON CONFLICT(id) DO UPDATE SET
                 schema_version=excluded.schema_version,
                 status=excluded.status,
                 updated_at=excluded.updated_at,
                 payload_json=excluded.payload_json""",
            (
                brief_id,
                run_row["run_id"],
                run_row["thread_id"],
                schema_version,
                str(payload.get("brief_status") or "draft"),
                now,
                now,
                _canonical_json(brief_payload),
            ),
        )
        return brief_id

    async def _load_in_transaction(
        self,
        conn: aiosqlite.Connection,
        run_id: str,
    ) -> RunProjection:
        row = await self._fetch_run(conn, run_id)
        if row is None:
            raise LifecycleCommandConflict("Run does not exist")
        async with conn.execute(
            """SELECT id FROM content_research_briefs
               WHERE workflow_run_id=? ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (run_id,),
        ) as cursor:
            brief = await cursor.fetchone()
        async with conn.execute(
            """SELECT id FROM content_research_scope_contracts
               WHERE workflow_run_id=? ORDER BY version DESC LIMIT 1""",
            (run_id,),
        ) as cursor:
            scope = await cursor.fetchone()
        async with conn.execute(
            "SELECT workflow_run_id, attempt_count "
            "FROM content_research_dispatch_jobs WHERE workflow_run_id=? LIMIT 1",
            (run_id,),
        ) as cursor:
            dispatch = await cursor.fetchone()
        async with conn.execute(
            """SELECT a.execution_unit_id, a.attempt_no
               FROM content_research_scope_execution_attempts AS a
               JOIN content_research_scope_execution_units AS u
                 ON u.id=a.execution_unit_id
               WHERE u.workflow_run_id=?
               ORDER BY a.attempt_no DESC LIMIT 1""",
            (run_id,),
        ) as cursor:
            attempt = await cursor.fetchone()
        async with conn.execute(
            """SELECT payload_json FROM workflow_artifacts
               WHERE run_id=? AND artifact_type='final_result'
                 AND artifact_version=? LIMIT 1""",
            (run_id, int(row["artifact_version"] or 0)),
        ) as cursor:
            publication = await cursor.fetchone()
        return projection_from_row(
            row,
            brief_id=str(brief["id"]) if brief else None,
            scope_contract_id=str(scope["id"]) if scope else None,
            has_dispatch=dispatch is not None,
            dispatch_attempt_id=(
                f"{dispatch['workflow_run_id']}:{dispatch['attempt_count']}"
                if dispatch
                else None
            ),
            execution_attempt_id=(
                f"{attempt['execution_unit_id']}:{attempt['attempt_no']}" if attempt else None
            ),
            publication_id=_publication_id_from_artifact_payload(
                publication["payload_json"] if publication else None
            ),
        )

    @staticmethod
    async def _fetch_run(
        conn: aiosqlite.Connection,
        run_id: str,
    ) -> aiosqlite.Row | None:
        async with conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id=?",
            (run_id,),
        ) as cursor:
            return await cursor.fetchone()

    @staticmethod
    async def _fetch_command(
        conn: aiosqlite.Connection,
        command_id: str,
    ) -> aiosqlite.Row | None:
        async with conn.execute(
            "SELECT * FROM content_research_lifecycle_commands WHERE command_id=?",
            (command_id,),
        ) as cursor:
            return await cursor.fetchone()
