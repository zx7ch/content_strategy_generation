"""Read-only, transactionally consistent snapshots for single-writer data."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Generic, TypeVar

from app.core.sqlite_connection_roles import open_readonly_database


@dataclass(frozen=True)
class DiagnosticSnapshot:
    run_id: str
    observed_revision: int
    fact_ids: tuple[str, ...]


SnapshotT = TypeVar("SnapshotT")


@dataclass(frozen=True)
class SnapshotFound(Generic[SnapshotT]):
    snapshot: SnapshotT


@dataclass(frozen=True)
class SnapshotNotFound:
    run_id: str


@dataclass(frozen=True)
class SnapshotBehind:
    observed_revision: int
    minimum_revision: int


@dataclass(frozen=True)
class SnapshotUnavailable:
    code: str = "SNAPSHOT_UNAVAILABLE"


DiagnosticSnapshotReadResult = (
    SnapshotFound[DiagnosticSnapshot]
    | SnapshotNotFound
    | SnapshotBehind
    | SnapshotUnavailable
)

DomainTraceLoader = Callable[
    [sqlite3.Connection, str], Awaitable[tuple[SnapshotT, int] | None]
]
DomainTraceReadResult = (
    SnapshotFound[SnapshotT] | SnapshotNotFound | SnapshotBehind | SnapshotUnavailable
)


class ConsistentSnapshotReader:
    """Compose a closed projection from exactly one read transaction."""

    def __init__(
        self,
        database_path: Path,
        *,
        domain_trace_loader: DomainTraceLoader | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._domain_trace_loader = domain_trace_loader

    async def read_domain_trace(
        self,
        run_id: str,
        minimum_revision: int | None = None,
        *,
        wait_timeout: float = 0.2,
    ) -> DomainTraceReadResult:
        """Return one causally bounded Domain Trace from a single WAL snapshot."""

        deadline = time.monotonic() + max(0.0, wait_timeout)
        last_revision = 0
        while True:
            result = await self._read_domain_trace_once(run_id)
            if isinstance(result, SnapshotUnavailable | SnapshotNotFound):
                return result
            snapshot, observed_revision = result
            last_revision = observed_revision
            if minimum_revision is None or observed_revision >= minimum_revision:
                return SnapshotFound(snapshot)
            if time.monotonic() >= deadline:
                return SnapshotBehind(last_revision, minimum_revision)
            await asyncio.sleep(0.01)

    async def _read_domain_trace_once(
        self, run_id: str
    ) -> tuple[SnapshotT, int] | SnapshotNotFound | SnapshotUnavailable:
        if self._domain_trace_loader is None:
            return SnapshotUnavailable("DOMAIN_TRACE_PROJECTION_FAILED")
        try:
            with open_readonly_database(
                self._database_path,
                timeout=0.2,
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA busy_timeout=200")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
                loaded = await self._domain_trace_loader(connection, run_id)
                connection.rollback()
                if loaded is None:
                    return SnapshotNotFound(run_id)
                return loaded
        except (OSError, sqlite3.DatabaseError):
            return SnapshotUnavailable()
        except Exception:
            return SnapshotUnavailable("DOMAIN_TRACE_PROJECTION_FAILED")

    def read_diagnostic_snapshot(
        self,
        run_id: str,
        minimum_revision: int | None = None,
        *,
        wait_timeout: float = 0.2,
    ) -> DiagnosticSnapshotReadResult:
        deadline = time.monotonic() + max(0.0, wait_timeout)
        last_revision = 0
        while True:
            result = self._read_once(run_id)
            if isinstance(result, SnapshotUnavailable | SnapshotNotFound):
                return result
            last_revision = result.snapshot.observed_revision
            if minimum_revision is None or last_revision >= minimum_revision:
                return result
            if time.monotonic() >= deadline:
                return SnapshotBehind(last_revision, minimum_revision)
            time.sleep(0.01)

    def _read_once(
        self,
        run_id: str,
    ) -> SnapshotFound | SnapshotNotFound | SnapshotUnavailable:
        try:
            with open_readonly_database(
                self._database_path,
                timeout=0.2,
            ) as connection:
                connection.execute("BEGIN")
                revision_row = connection.execute(
                    "SELECT revision FROM run_revisions WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if revision_row is None:
                    connection.rollback()
                    return SnapshotNotFound(run_id)
                fact_ids = tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT mutation_id FROM diagnostic_facts "
                        "WHERE run_id=? ORDER BY rowid",
                        (run_id,),
                    )
                )
                observed_revision = int(revision_row[0])
                if len(fact_ids) != observed_revision:
                    connection.rollback()
                    return SnapshotUnavailable()
                connection.rollback()
                return SnapshotFound(
                    DiagnosticSnapshot(
                        run_id=run_id,
                        observed_revision=observed_revision,
                        fact_ids=fact_ids,
                    )
                )
        except (OSError, sqlite3.DatabaseError):
            return SnapshotUnavailable()
