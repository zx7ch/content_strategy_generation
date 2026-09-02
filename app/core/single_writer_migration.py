"""Isolated forward migration boundary for the frozen Task 3.1 database."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from app.core.sqlite_connection_roles import open_migration_database
from app.core.sqlite_runtime_lock import (
    RuntimeDatabaseLockedError,
    SQLiteRuntimeProcessLock,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Literal


_TASK_3_1_MIGRATION_VERSIONS = tuple(f"{version:04d}" for version in range(1, 39))
_TERMINAL_CONTENT_RESEARCH_STATES = frozenset(
    {"report_ready", "cancelled_or_failed"}
)
_MANIFEST_NAME = "runtime-data-manifest.json"
_USER_DATA_FAMILY_TABLES: dict[str, tuple[str, ...]] = {
    "workspaces": ("md_workspaces",),
    "brands": ("md_brands",),
    "channels": ("md_brand_channels",),
    "policies": ("md_policy_configs",),
    "threads": ("creator_threads",),
    "messages": ("creator_messages",),
    "runs": ("workflow_runs",),
    "artifacts": ("workflow_artifacts",),
    "reports": ("content_research_report_publications",),
    "citations": ("content_research_evidence_lineage",),
    "decisions": (
        "content_research_human_decisions",
        "content_research_marketing_conclusion_decisions",
    ),
    "publish_candidates": ("publish_candidates",),
    "configurations": ("content_research_llm_configurations",),
    "credentials": ("xhs_local_credentials",),
    "usage": ("llm_usage_events", "content_research_budget_ledger_entries"),
}


class MigrationSourceUnsupportedError(RuntimeError):
    error_code = "MIGRATION_SOURCE_SCHEMA_UNSUPPORTED"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class MigrationIncompleteRunsError(RuntimeError):
    error_code = "MIGRATION_INCOMPLETE_RUNS_PRESENT"

    def __init__(self, run_ids: tuple[str, ...]) -> None:
        self.run_ids = run_ids
        super().__init__(self.error_code)


class MigrationSourceBusyError(RuntimeError):
    error_code = "MIGRATION_SOURCE_BUSY"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class MigrationValidationError(RuntimeError):
    error_code = "MIGRATION_VALIDATION_FAILED"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class InjectedMigrationCrash(RuntimeError):
    """Fault used only by process-level migration acceptance tests."""


@dataclass(frozen=True)
class MigrationAssessment:
    source_kind: str
    schema_version: str | None
    incomplete_run_ids: tuple[str, ...]
    requires_incomplete_policy: bool
    family_counts: Mapping[str, int]


@dataclass(frozen=True)
class MigrationReceipt:
    receipt_id: str
    status: str
    source_fingerprint: str
    backup_fingerprint: str
    target_fingerprint: str
    archived_run_ids: tuple[str, ...]
    imported_family_counts: Mapping[str, int]

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "status": self.status,
            "source_fingerprint": self.source_fingerprint,
            "backup_fingerprint": self.backup_fingerprint,
            "target_fingerprint": self.target_fingerprint,
            "archived_run_ids": list(self.archived_run_ids),
            "imported_family_counts": dict(self.imported_family_counts),
        }


def _readonly_connection(source: Path) -> sqlite3.Connection:
    # A cleanly closed WAL database has no sidecar and can be opened immutable,
    # which avoids SQLite creating empty ``-wal``/``-shm`` files during inspect.
    # If a WAL exists, ordinary read-only mode is required so committed frames
    # remain visible; ``immutable=1`` deliberately ignores those frames.
    wal_path = source.with_name(f"{source.name}-wal")
    connection = open_migration_database(
        source,
        readonly=True,
        immutable=not wal_path.exists(),
    )
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if table != "workflow_runs":
        raise MigrationSourceUnsupportedError()
    return {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(workflow_runs)")
    }


def _user_data_family_counts(
    connection: sqlite3.Connection,
    tables: set[str],
) -> Mapping[str, int]:
    return MappingProxyType(
        {
            family: sum(
                int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in family_tables
                if table in tables
            )
            for family, family_tables in _USER_DATA_FAMILY_TABLES.items()
        }
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _snapshot_sidecars(path: Path) -> tuple[Path, Path]:
    return (
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )


def _materialize_source_snapshot(source: Path, snapshot: Path) -> None:
    """Copy a stopped database family before asking SQLite to recover its WAL."""

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, snapshot)
    source_wal, _source_shm = _snapshot_sidecars(source)
    snapshot_wal, snapshot_shm = _snapshot_sidecars(snapshot)
    if source_wal.exists():
        shutil.copyfile(source_wal, snapshot_wal)
    try:
        with closing(open_migration_database(snapshot)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.commit()
    finally:
        for sidecar in (snapshot_wal, snapshot_shm):
            if sidecar.exists():
                sidecar.unlink()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SingleWriterDataMigrator:
    """Inspect and migrate the one supported pre-single-writer data generation."""

    def __init__(self, fault_injector: Callable[[str], None] | None = None) -> None:
        self._fault_injector = fault_injector

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    def inspect(self, source_path: Path) -> MigrationAssessment:
        source = source_path.expanduser()
        if not source.exists():
            return MigrationAssessment(
                source_kind="fresh",
                schema_version=None,
                incomplete_run_ids=(),
                requires_incomplete_policy=False,
                family_counts=MappingProxyType({}),
            )
        try:
            with tempfile.TemporaryDirectory(prefix="single-writer-inspect-") as root:
                snapshot = Path(root) / source.name
                _materialize_source_snapshot(source, snapshot)
                connection_context = closing(
                    open_migration_database(
                        snapshot,
                        readonly=True,
                        immutable=True,
                    )
                )
                with connection_context as connection:
                    connection.row_factory = sqlite3.Row
                    return self._inspect_connection(connection)
        except (sqlite3.DatabaseError, OSError) as exc:
            raise MigrationSourceUnsupportedError() from exc

    @staticmethod
    def _inspect_connection(connection: sqlite3.Connection) -> MigrationAssessment:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required_tables = {
            "content_research_schema_migrations",
            "creator_threads",
            "workflow_artifacts",
            "workflow_runs",
        }
        if not required_tables.issubset(tables):
            raise MigrationSourceUnsupportedError()
        versions = tuple(
            str(row["version"])
            for row in connection.execute(
                "SELECT version FROM content_research_schema_migrations "
                "ORDER BY version"
            )
        )
        if versions != _TASK_3_1_MIGRATION_VERSIONS:
            raise MigrationSourceUnsupportedError()
        if not {"run_id", "content_research_state"}.issubset(
            _table_columns(connection, "workflow_runs")
        ):
            raise MigrationSourceUnsupportedError()
        incomplete_run_ids = tuple(
            str(row["run_id"])
            for row in connection.execute(
                "SELECT run_id FROM workflow_runs "
                "WHERE content_research_state IS NOT NULL "
                "AND content_research_state NOT IN (?, ?) ORDER BY run_id",
                tuple(sorted(_TERMINAL_CONTENT_RESEARCH_STATES)),
            )
        )
        family_counts = dict(_user_data_family_counts(connection, tables))
        family_counts["runs"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM workflow_runs "
                "WHERE content_research_state IS NOT NULL"
            ).fetchone()[0]
        )
        family_counts = MappingProxyType(family_counts)
        return MigrationAssessment(
            source_kind="task_3_1",
            schema_version=_TASK_3_1_MIGRATION_VERSIONS[-1],
            incomplete_run_ids=incomplete_run_ids,
            requires_incomplete_policy=bool(incomplete_run_ids),
            family_counts=family_counts,
        )

    def migrate(
        self,
        source_path: Path,
        target_path: Path,
        incomplete_policy: Literal["archive_incomplete"] | None,
    ) -> MigrationReceipt:
        source = source_path.expanduser()
        target = target_path.expanduser()
        assessment = self.inspect(source)
        if assessment.source_kind != "task_3_1":
            raise MigrationSourceUnsupportedError()
        self._require_incomplete_policy(assessment, incomplete_policy)

        source_lock = SQLiteRuntimeProcessLock(str(source))
        try:
            source_lock.acquire()
        except RuntimeDatabaseLockedError as exc:
            raise MigrationSourceBusyError() from exc
        try:
            assessment = self.inspect(source)
            self._require_incomplete_policy(assessment, incomplete_policy)
            activated = self._read_activated_receipt(target)
            if activated is not None:
                return activated
            self._fault("before_backup")
            backup, backup_fingerprint = self._create_backup(source, target.parent)
            source_fingerprint = backup_fingerprint
            policy_identity = incomplete_policy or "no_incomplete_runs"
            receipt_id = "migration_" + hashlib.sha256(
                f"{backup_fingerprint}:{policy_identity}".encode()
            ).hexdigest()[:24]
            receipt = self._create_target(
                target=target,
                source_snapshot=backup,
                assessment=assessment,
                source_fingerprint=source_fingerprint,
                backup_fingerprint=backup_fingerprint,
                receipt_id=receipt_id,
            )
            self._activate_manifest(target, receipt)
            return receipt
        finally:
            source_lock.release()

    @staticmethod
    def _require_incomplete_policy(
        assessment: MigrationAssessment,
        incomplete_policy: Literal["archive_incomplete"] | None,
    ) -> None:
        if assessment.incomplete_run_ids and incomplete_policy != "archive_incomplete":
            raise MigrationIncompleteRunsError(assessment.incomplete_run_ids)

    def _create_backup(self, source: Path, data_root: Path) -> tuple[Path, str]:
        data_root.mkdir(parents=True, exist_ok=True)
        backup_root = data_root / "migration-backups"
        backup_root.mkdir(exist_ok=True)
        partial = backup_root / ".task-3-1-snapshot.partial"
        partial_sidecars = _snapshot_sidecars(partial)
        for candidate in (partial, *partial_sidecars):
            if candidate.exists():
                candidate.unlink()
        try:
            _materialize_source_snapshot(source, partial)
            backup_fingerprint = _sha256(partial)
            backup = backup_root / f"task-3-1-{backup_fingerprint[7:23]}.sqlite"
            if backup.exists():
                if _sha256(backup) != backup_fingerprint:
                    raise MigrationValidationError()
                partial.unlink()
                return backup, backup_fingerprint
            os.replace(partial, backup)
            backup.chmod(backup.stat().st_mode & ~0o222)
            _fsync_directory(backup_root)
            return backup, backup_fingerprint
        finally:
            for candidate in (partial, *partial_sidecars):
                if candidate.exists():
                    candidate.unlink()

    def _create_target(
        self,
        *,
        target: Path,
        source_snapshot: Path,
        assessment: MigrationAssessment,
        source_fingerprint: str,
        backup_fingerprint: str,
        receipt_id: str,
    ) -> MigrationReceipt:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.{receipt_id}.partial")
        partial_sidecars = _snapshot_sidecars(partial)
        for candidate in (partial, *partial_sidecars):
            if candidate.exists():
                candidate.unlink()
        try:
            with closing(_readonly_connection(source_snapshot)) as source_connection:
                with closing(open_migration_database(partial)) as target_connection:
                    source_connection.backup(target_connection)
                    target_connection.commit()
            self._fault("during_import")

            # Release-copy fixtures can carry the additive column from a newer
            # bootstrap while their frozen migration ledger and table set still
            # identify Task 3.1. Normalize only the unactivated target copy so
            # the immutable 0039 migration remains the sole schema authority.
            with closing(open_migration_database(partial)) as connection:
                packet_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(content_research_directional_evidence_packets)"
                    )
                }
                source_observation_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='content_research_source_observations'"
                ).fetchone()
                if (
                    "source_observation_id" in packet_columns
                    and source_observation_table is None
                ):
                    connection.execute(
                        "ALTER TABLE content_research_directional_evidence_packets "
                        "DROP COLUMN source_observation_id"
                    )
                    connection.commit()

            from app.core.runtime_schema_bootstrap import (
                bootstrap_canonical_runtime_schema,
            )

            asyncio.run(
                bootstrap_canonical_runtime_schema(
                    partial,
                    discovery_secret="single-writer-migration",
                )
            )
            with closing(open_migration_database(partial)) as connection:
                connection.executescript(
                    """
                    PRAGMA synchronous=FULL;
                    CREATE TABLE IF NOT EXISTS runtime_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS migration_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        source_fingerprint TEXT NOT NULL,
                        backup_fingerprint TEXT NOT NULL,
                        archived_run_ids_json TEXT NOT NULL,
                        family_counts_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS archived_runs (
                        run_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        public_failure_code TEXT NOT NULL,
                        read_only INTEGER NOT NULL CHECK(read_only = 1)
                    );
                    """
                )
                connection.execute(
                    "INSERT OR REPLACE INTO runtime_metadata(key, value) VALUES (?, ?)",
                    ("layout", "single_writer"),
                )
                for run_id in assessment.incomplete_run_ids:
                    row = connection.execute(
                        "SELECT thread_id, content_research_state, "
                        "COALESCE(state_revision, 0) FROM workflow_runs WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    if row is None:
                        raise MigrationValidationError()
                    next_revision = int(row[2]) + 1
                    connection.execute(
                        "UPDATE workflow_runs SET status='failed', phase='finalization', "
                        "current_step='upgrade_interrupted', active_job_id=NULL, "
                        "active_job_type=NULL, error_code='UPGRADE_INTERRUPTED', "
                        "error_message='Run archived during runtime upgrade', "
                        "content_research_state='cancelled_or_failed', state_revision=?, "
                        "state_entered_at=CURRENT_TIMESTAMP, "
                        "lifecycle_error_json=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE run_id=?",
                        (
                            next_revision,
                            json.dumps(
                                {
                                    "code": "UPGRADE_INTERRUPTED",
                                    "retryable": False,
                                    "stage": "runtime_upgrade",
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            run_id,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO content_research_state_transitions "
                        "(run_id, thread_id, from_state, to_state, event, "
                        "state_revision, reason_code, error_json, created_at) "
                        "VALUES (?, ?, ?, 'cancelled_or_failed', 'archive_incomplete_upgrade', "
                        "?, 'UPGRADE_INTERRUPTED', ?, CURRENT_TIMESTAMP)",
                        (
                            run_id,
                            str(row[0]),
                            str(row[1]),
                            next_revision,
                            json.dumps(
                                {"code": "UPGRADE_INTERRUPTED", "retryable": False},
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                        ),
                    )
                    connection.execute(
                        "UPDATE creator_threads SET active_run_id=NULL "
                        "WHERE id=? AND active_run_id=?",
                        (str(row[0]), run_id),
                    )
                    connection.execute(
                        "INSERT INTO archived_runs "
                        "(run_id, status, public_failure_code, read_only) "
                        "VALUES (?, 'UPGRADE_INTERRUPTED', 'upgrade_interrupted', 1)",
                        (run_id,),
                    )
                connection.execute(
                    "INSERT INTO migration_receipts VALUES (?, ?, ?, ?, ?)",
                    (
                        receipt_id,
                        source_fingerprint,
                        backup_fingerprint,
                        json.dumps(assessment.incomplete_run_ids, separators=(",", ":")),
                        json.dumps(dict(assessment.family_counts), separators=(",", ":")),
                    ),
                )
                connection.commit()
                checkpoint = connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if checkpoint != (0, 0, 0):
                    raise MigrationValidationError()
            self._fault("during_validation")
            self._validate_target(partial, assessment)
            os.replace(partial, target)
            _fsync_directory(target.parent)
            target_fingerprint = _sha256(target)
            return MigrationReceipt(
                receipt_id=receipt_id,
                status="activated",
                source_fingerprint=source_fingerprint,
                backup_fingerprint=backup_fingerprint,
                target_fingerprint=target_fingerprint,
                archived_run_ids=assessment.incomplete_run_ids,
                imported_family_counts=MappingProxyType(dict(assessment.family_counts)),
            )
        finally:
            for candidate in (partial, *partial_sidecars):
                if candidate.exists():
                    candidate.unlink()

    @staticmethod
    def _validate_target(target: Path, assessment: MigrationAssessment) -> None:
        try:
            with closing(
                open_migration_database(target, readonly=True)
            ) as connection:
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise MigrationValidationError()
                archived = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT run_id FROM archived_runs ORDER BY run_id"
                    )
                )
                if archived != assessment.incomplete_run_ids:
                    raise MigrationValidationError()
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                actual_counts = _user_data_family_counts(connection, tables)
                actual_counts = dict(actual_counts)
                actual_counts["runs"] = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM workflow_runs "
                        "WHERE content_research_state IS NOT NULL"
                    ).fetchone()[0]
                )
                for family, expected in assessment.family_counts.items():
                    if actual_counts.get(family) != expected:
                        raise MigrationValidationError()
                triggers = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'cr_trace_revision_%' LIMIT 1"
                ).fetchone()
                if triggers is not None:
                    raise MigrationValidationError()
        except sqlite3.DatabaseError as exc:
            raise MigrationValidationError() from exc

    def _activate_manifest(self, target: Path, receipt: MigrationReceipt) -> None:
        self._fault("before_manifest_activation")
        manifest = target.parent / _MANIFEST_NAME
        partial = manifest.with_suffix(".partial")
        payload = {
            "database": target.name,
            "layout": "single_writer",
            "receipt_id": receipt.receipt_id,
            "target_fingerprint": receipt.target_fingerprint,
        }
        try:
            partial.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            with partial.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(partial, manifest)
            _fsync_directory(target.parent)
        finally:
            if partial.exists():
                partial.unlink()

    @staticmethod
    def _read_activated_receipt(target: Path) -> MigrationReceipt | None:
        manifest = target.parent / _MANIFEST_NAME
        if not manifest.exists():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if (
                payload.get("layout") != "single_writer"
                or payload.get("database") != target.name
                or not target.exists()
                or payload.get("target_fingerprint") != _sha256(target)
            ):
                raise MigrationValidationError()
            with closing(
                open_migration_database(target, readonly=True)
            ) as connection:
                row = connection.execute(
                    "SELECT receipt_id, source_fingerprint, backup_fingerprint, "
                    "archived_run_ids_json, family_counts_json FROM migration_receipts "
                    "WHERE receipt_id=?",
                    (payload["receipt_id"],),
                ).fetchone()
            if row is None:
                raise MigrationValidationError()
            return MigrationReceipt(
                receipt_id=str(row[0]),
                status="activated",
                source_fingerprint=str(row[1]),
                backup_fingerprint=str(row[2]),
                target_fingerprint=str(payload["target_fingerprint"]),
                archived_run_ids=tuple(json.loads(row[3])),
                imported_family_counts=MappingProxyType(json.loads(row[4])),
            )
        except (KeyError, OSError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
            raise MigrationValidationError() from exc
