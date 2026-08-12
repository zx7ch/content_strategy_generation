"""Async persistence session for a single directional execution.

The pipeline keeps its deterministic selection logic synchronous, but all
SQLite I/O is loaded and flushed by ``aiosqlite`` at explicit behavior
boundaries.  Provider-operation facts are flushed before and after calls.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TypeVar

import aiosqlite

from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    StageCheckpointRecord,
    TypedPersistenceRecord,
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

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._records: dict[type[TypedPersistenceRecord], dict[str, TypedPersistenceRecord]] = defaultdict(dict)
        self._pending: list[TypedPersistenceRecord] = []

    @classmethod
    async def open(
        cls, db_path: str, *, workflow_run_id: str | None = None
    ) -> AsyncDirectionalPersistenceSession:
        session = cls(db_path)
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            for record_type, (table, fields) in _TYPED_RECORD_TABLES.items():
                if workflow_run_id is not None and "workflow_run_id" in fields:
                    cursor = await conn.execute(
                        f"SELECT * FROM {table} WHERE workflow_run_id=? ORDER BY created_at ASC, id ASC",
                        (workflow_run_id,),
                    )
                else:
                    cursor = await conn.execute(f"SELECT * FROM {table} ORDER BY created_at ASC, id ASC")
                for row in await cursor.fetchall():
                    session._records[record_type][str(row["id"])] = session._row_to_record(
                        row, record_type, fields
                    )
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

    def _save(self, record: T) -> T:
        bucket = self._records[type(record)]
        existing = bucket.get(record.id)
        if existing == record:
            return record
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
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                for record in pending:
                    table, fields = _TYPED_RECORD_TABLES[type(record)]
                    values = [getattr(record, field) for field in fields]
                    values = [
                        _fmt_dt(value) if field in {"started_at", "finished_at"} and value else value
                        for field, value in zip(fields, values, strict=True)
                    ]
                    columns = ("id", "schema_version", *fields, "payload_json", "metadata_json", "created_at")
                    if isinstance(record, StageCheckpointRecord):
                        updates = ", ".join(
                            f"{column}=excluded.{column}"
                            for column in columns
                            if column != "id"
                        )
                        conflict_clause = (
                            f"DO UPDATE SET {updates} "
                            f"WHERE {table}.status='superseded'"
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
                        raise RuntimeError(
                            f"immutable persistence conflict for {table}:{record.id}"
                        )
                await conn.commit()
            except Exception:
                await conn.rollback()
                self._pending = [*pending, *self._pending]
                raise

    @staticmethod
    def _row_to_record(
        row: aiosqlite.Row, record_type: type[TypedPersistenceRecord], fields: tuple[str, ...]
    ) -> TypedPersistenceRecord:
        values = {
            field: _parse_dt(row[field]) if field in {"started_at", "finished_at"} and row[field] else row[field]
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
