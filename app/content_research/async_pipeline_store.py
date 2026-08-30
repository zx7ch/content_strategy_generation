"""Async persistence session for a single directional execution.

The pipeline keeps its deterministic selection logic synchronous, but all
SQLite I/O is loaded and flushed by ``aiosqlite`` at explicit behavior
boundaries.  Provider-operation facts are flushed before and after calls.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import TypeVar

import aiosqlite

from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    StageCheckpointRecord,
    TypedPersistenceRecord,
)
from app.content_research.scope_contract import (
    DispatchLeaseContext,
    ExecutionContext,
    ExecutionLeaseFencedError,
    ScopeAuditEvent,
)
from app.content_research.stores.sqlite_store import (
    _TYPED_RECORD_TABLES,
    _dumps,
    _fmt_dt,
    _loads,
    _parse_dt,
)

T = TypeVar("T", bound=TypedPersistenceRecord)


class AsyncDirectionalPersistenceSession:
    """In-memory typed-record view backed by asynchronous, explicit flushes."""

    def __init__(
        self,
        db_path: str,
        *,
        execution_context: ExecutionContext | None = None,
        dispatch_context: DispatchLeaseContext | None = None,
    ) -> None:
        self._db_path = db_path
        self._execution_context = execution_context
        self._dispatch_context = dispatch_context
        self._records: dict[type[TypedPersistenceRecord], dict[str, TypedPersistenceRecord]] = (
            defaultdict(dict)
        )
        self._pending: list[TypedPersistenceRecord] = []
        self._pending_scope_events: list[ScopeAuditEvent] = []
        self._scope_event_ids: set[str] = set()

    @classmethod
    async def open(
        cls,
        db_path: str,
        *,
        workflow_run_id: str | None = None,
        execution_context: ExecutionContext | None = None,
        dispatch_context: DispatchLeaseContext | None = None,
    ) -> AsyncDirectionalPersistenceSession:
        session = cls(
            db_path,
            execution_context=execution_context,
            dispatch_context=dispatch_context,
        )
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            for record_type, (table, fields) in _TYPED_RECORD_TABLES.items():
                if workflow_run_id is not None and "workflow_run_id" in fields:
                    cursor = await conn.execute(
                        f"SELECT * FROM {table} WHERE workflow_run_id=? ORDER BY created_at ASC, id ASC",
                        (workflow_run_id,),
                    )
                else:
                    cursor = await conn.execute(
                        f"SELECT * FROM {table} ORDER BY created_at ASC, id ASC"
                    )
                for row in await cursor.fetchall():
                    session._records[record_type][str(row["id"])] = session._row_to_record(
                        row, record_type, fields
                    )
            cursor = await conn.execute(
                "SELECT id FROM content_research_scope_audit_events"
                + (" WHERE workflow_run_id=?" if workflow_run_id is not None else ""),
                (workflow_run_id,) if workflow_run_id is not None else (),
            )
            session._scope_event_ids = {str(row["id"]) for row in await cursor.fetchall()}
        return session

    def get_typed_record(self, record_type: type[T], record_id: str) -> T | None:
        return self._records[record_type].get(record_id)  # type: ignore[return-value]

    def list_typed_records(self, record_type: type[T]) -> list[T]:
        return list(self._records[record_type].values())  # type: ignore[return-value]

    def resolve_canonical_source(self, source: CanonicalSourceRecord) -> CanonicalSourceRecord:
        existing = next(
            (
                item
                for item in self._records[CanonicalSourceRecord].values()
                if item.platform == source.platform
                and item.platform_source_kind == source.platform_source_kind
                and item.platform_source_id == source.platform_source_id
            ),
            None,
        )
        if existing is not None:
            return existing  # type: ignore[return-value]
        self._save(source)
        return source

    def save_direction_source_projection(self, record: T) -> T:
        return self._save(record)

    def save_directional_evidence_packet(self, record: T) -> T:
        return self._save(record)

    def save_claim_candidate(self, record: T) -> T:
        return self._save(record)

    def save_claim_admission_decision(self, record: T) -> T:
        return self._save(record)

    def save_direction_result_decision(self, record: T) -> T:
        return self._save(record)

    def save_weak_signal(self, record: T) -> T:
        return self._save(record)

    def save_stage_checkpoint(self, record: T) -> T:
        return self._save(record)

    def append_scope_audit_event(self, event: ScopeAuditEvent) -> ScopeAuditEvent:
        if event.id not in self._scope_event_ids:
            self._scope_event_ids.add(event.id)
            self._pending_scope_events.append(event)
        return event

    def _save(self, record: T) -> T:
        bucket = self._records[type(record)]
        existing = bucket.get(record.id)
        if existing == record:
            return record
        if (
            isinstance(existing, StageCheckpointRecord)
            and isinstance(record, StageCheckpointRecord)
            and _checkpoint_ownership(existing) != _checkpoint_ownership(record)
        ):
            raise ValueError("stage checkpoint has immutable execution ownership")
        if existing is not None and not (
            isinstance(existing, StageCheckpointRecord)
            and isinstance(record, StageCheckpointRecord)
            and existing.status == "superseded"
        ):
            # Governed evidence facts are immutable. Stable-ID replacement is
            # reserved for a checkpoint explicitly retired for same-run replay.
            return existing  # type: ignore[return-value]
        bucket[record.id] = record
        self._pending.append(record)
        return record

    async def flush(self) -> None:
        if not self._pending and not self._pending_scope_events:
            return
        pending, self._pending = self._pending, []
        pending_scope_events, self._pending_scope_events = self._pending_scope_events, []
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            try:
                if self._execution_context is not None:
                    context = self._execution_context
                    cursor = await conn.execute(
                        """SELECT 1 FROM content_research_scope_execution_attempts AS attempt
                           JOIN content_research_scope_execution_units AS unit
                             ON unit.id=attempt.execution_unit_id
                           WHERE attempt.execution_unit_id=? AND attempt.attempt_no=?
                             AND attempt.attempt_no=(
                               SELECT MAX(latest.attempt_no)
                               FROM content_research_scope_execution_attempts AS latest
                               WHERE latest.execution_unit_id=attempt.execution_unit_id
                             )
                             AND attempt.state='running' AND unit.state='running'
                             AND attempt.lease_token=? AND attempt.lease_expires_at > ?
                             AND unit.scope_contract_id=?""",
                        (
                            context.execution_unit_id,
                            context.attempt_no,
                            context.lease_token,
                            _fmt_dt(datetime.now(timezone.utc)),
                            context.scope_contract_id,
                        ),
                    )
                    if await cursor.fetchone() is None:
                        sequence_cursor = await conn.execute(
                            """SELECT COALESCE(MAX(sequence_no), 0) + 1
                               FROM content_research_execution_facts
                               WHERE execution_unit_id=? AND attempt_no=?""",
                            (context.execution_unit_id, context.attempt_no),
                        )
                        sequence_no = int((await sequence_cursor.fetchone())[0])
                        await conn.execute(
                            """INSERT INTO content_research_execution_facts
                               (execution_unit_id, attempt_no, sequence_no, kind,
                                payload_json, created_at)
                               VALUES (?, ?, ?, 'lease_fenced', ?, ?)""",
                            (
                                context.execution_unit_id,
                                context.attempt_no,
                                sequence_no,
                                _dumps({"operation": "directional_persistence_flush"}),
                                _fmt_dt(datetime.now(timezone.utc)),
                            ),
                        )
                        await conn.commit()
                        raise ExecutionLeaseFencedError(
                            "execution attempt lease was fenced before directional persistence"
                        )
                if self._dispatch_context is not None:
                    context = self._dispatch_context
                    cursor = await conn.execute(
                        """SELECT 1 FROM content_research_dispatch_jobs
                           WHERE workflow_run_id=? AND status='running'
                             AND lease_owner=? AND lease_token=?
                             AND lease_expires_at IS NOT NULL AND lease_expires_at > ?""",
                        (
                            context.workflow_run_id,
                            context.lease_owner,
                            context.lease_token,
                            _fmt_dt(datetime.now(timezone.utc)),
                        ),
                    )
                    if await cursor.fetchone() is None:
                        await conn.rollback()
                        raise ExecutionLeaseFencedError(
                            "dispatch lease was fenced before directional persistence"
                        )
                for record in pending:
                    table, fields = _TYPED_RECORD_TABLES[type(record)]
                    values = [getattr(record, field) for field in fields]
                    values = [
                        _fmt_dt(value)
                        if field in {"started_at", "finished_at"} and value
                        else value
                        for field, value in zip(fields, values, strict=True)
                    ]
                    columns = (
                        "id",
                        "schema_version",
                        *fields,
                        "payload_json",
                        "metadata_json",
                        "created_at",
                    )
                    if isinstance(record, StageCheckpointRecord):
                        ownership_columns = {
                            "workflow_run_id",
                            "scope_contract_id",
                            "execution_unit_id",
                            "attempt_no",
                            "execution_revision",
                        }
                        updates = ", ".join(
                            f"{column}=excluded.{column}"
                            for column in columns
                            if column != "id" and column not in ownership_columns
                        )
                        conflict_clause = (
                            f"DO UPDATE SET {updates} WHERE {table}.status='superseded' "
                            f"AND {table}.workflow_run_id IS excluded.workflow_run_id "
                            f"AND {table}.scope_contract_id IS excluded.scope_contract_id "
                            f"AND {table}.execution_unit_id IS excluded.execution_unit_id "
                            f"AND {table}.attempt_no IS excluded.attempt_no "
                            f"AND {table}.execution_revision IS excluded.execution_revision"
                        )
                    else:
                        conflict_clause = "DO NOTHING"
                    result = await conn.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join('?' for _ in columns)}) "
                        f"ON CONFLICT(id) {conflict_clause}",
                        (
                            record.id,
                            record.schema_version,
                            *values,
                            _dumps(record.payload),
                            _dumps(record.metadata),
                            _fmt_dt(record.created_at),
                        ),
                    )
                    if result.rowcount != 1:
                        cursor = await conn.execute(
                            f"SELECT schema_version, {', '.join(fields)}, "
                            f"payload_json, metadata_json FROM {table} WHERE id=?",
                            (record.id,),
                        )
                        existing = await cursor.fetchone()
                        fields_match = existing is not None and all(
                            existing[field] == value
                            for field, value in zip(fields, values, strict=True)
                        )
                        if existing is None or not (
                            str(existing["schema_version"]) == record.schema_version
                            and fields_match
                            and _loads(existing["payload_json"]) == record.payload
                            and _loads(existing["metadata_json"]) == record.metadata
                        ):
                            raise RuntimeError(
                                f"immutable persistence conflict for {table}:{record.id}"
                            )
                for event in pending_scope_events:
                    result = await conn.execute(
                        """INSERT INTO content_research_scope_audit_events
                           (id, workflow_run_id, scope_contract_id, scope_contract_version,
                            event_name, payload_json, metadata_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(id) DO NOTHING""",
                        (
                            event.id,
                            event.workflow_run_id,
                            event.scope_contract_id,
                            event.scope_contract_version,
                            event.event_name,
                            _dumps(event.payload),
                            _dumps(event.metadata or {}),
                            _fmt_dt(event.created_at),
                        ),
                    )
                    if result.rowcount != 1:
                        cursor = await conn.execute(
                            """SELECT workflow_run_id, scope_contract_id,
                                      scope_contract_version, event_name,
                                      payload_json, metadata_json
                               FROM content_research_scope_audit_events
                               WHERE id=?""",
                            (event.id,),
                        )
                        existing = await cursor.fetchone()
                        if existing is None or (
                            str(existing["workflow_run_id"]) != event.workflow_run_id
                            or str(existing["scope_contract_id"]) != event.scope_contract_id
                            or int(existing["scope_contract_version"])
                            != event.scope_contract_version
                            or str(existing["event_name"]) != event.event_name
                            or _loads(existing["payload_json"]) != event.payload
                            or _loads(existing["metadata_json"]) != (event.metadata or {})
                        ):
                            raise RuntimeError(
                                "immutable scope audit event conflict for " + event.id
                            )
                await conn.commit()
            except Exception:
                await conn.rollback()
                self._pending = [*pending, *self._pending]
                self._pending_scope_events = [
                    *pending_scope_events,
                    *self._pending_scope_events,
                ]
                raise

    @staticmethod
    def _row_to_record(
        row: aiosqlite.Row, record_type: type[TypedPersistenceRecord], fields: tuple[str, ...]
    ) -> TypedPersistenceRecord:
        values = {
            field: _parse_dt(row[field])
            if field in {"started_at", "finished_at"} and row[field]
            else row[field]
            for field in fields
        }
        return record_type(
            id=row["id"],
            schema_version=row["schema_version"],
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            **values,
        )


def _checkpoint_ownership(record: StageCheckpointRecord) -> tuple[object, ...]:
    return (
        record.workflow_run_id,
        record.scope_contract_id,
        record.execution_unit_id,
        record.attempt_no,
        record.execution_revision,
    )
