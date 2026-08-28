"""Unreachable Task 3.1 persistence boundary for frozen analysis inputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.persistence_models import (
    MarketingConclusionCandidateRecord,
    MarketingConclusionDecisionRecord,
    StageCheckpointRecord,
)

EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "content_research_evidence_snapshot_v1"
ANALYSIS_UNIT_SCHEMA_VERSION = "content_research_analysis_unit_v1"


class AnalysisIdentityConflictError(ValueError):
    """A stable analysis identity was replayed with different immutable input."""


class AnalysisActiveAttemptConflictError(RuntimeError):
    """A unit already has an active analysis execution."""


class AnalysisLeaseFencedError(RuntimeError):
    """A stale or expired analysis attempt tried to persist output."""


class _BorrowedSQLiteConnection:
    """Non-closing context wrapper for a coordinator-owned read transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, _exc_type, _exc_value, _traceback) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        if name == "close":
            return lambda: None
        return getattr(self._connection, name)


def _required(*values: str) -> None:
    if not all(value.strip() for value in values):
        raise ValueError("analysis persistence identity is required")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class FrozenEvidenceNoteInput:
    note_id: str
    account_id: str
    title: str
    body: str
    source_url: str
    captured_at: datetime
    query_provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.note_id, self.account_id, self.source_url)
        if self.captured_at.tzinfo is None:
            raise ValueError("evidence captured_at must be timezone-aware")
        if not self.query_provenance or any(not item.strip() for item in self.query_provenance):
            raise ValueError("evidence note requires query provenance")
        if len(set(self.query_provenance)) != len(self.query_provenance):
            raise ValueError("evidence query provenance must be unique")


@dataclass(frozen=True)
class FrozenEvidenceNote:
    note_id: str
    account_id: str
    title: str
    body: str
    title_hash: str
    body_hash: str
    source_url: str
    captured_at: datetime
    query_provenance: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceSnapshot:
    id: str
    schema_version: str
    workflow_run_id: str
    scope_contract_id: str
    retrieval_execution_unit_id: str
    retrieval_attempt_no: int
    snapshot_fingerprint: str
    query_groups: tuple[dict[str, Any], ...]
    notes: tuple[FrozenEvidenceNote, ...]
    created_at: datetime


@dataclass(frozen=True)
class AnalysisUnit:
    id: str
    schema_version: str
    workflow_run_id: str
    evidence_snapshot_id: str
    contract_fingerprint: str
    policy_version: str
    prompt_hash: str
    response_schema_hash: str
    embedding_fingerprint: dict[str, Any]
    algorithm_version: str
    verifier_version: str
    created_at: datetime


@dataclass(frozen=True)
class AnalysisAttempt:
    id: str
    analysis_unit_id: str
    attempt_no: int
    state: str
    successor_of_attempt_id: str | None
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: datetime | None
    created_at: datetime
    terminal_at: datetime | None


@dataclass(frozen=True)
class AnalysisJobContext:
    analysis_unit_id: str
    workflow_run_id: str
    research_plan_id: str
    coverage_snapshot_id: str
    execution_authorization_id: str | None
    manifest: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class AnalysisJobClaim:
    context: AnalysisJobContext
    attempt: AnalysisAttempt


@dataclass(frozen=True)
class AnalysisCheckpoint:
    id: str
    analysis_unit_id: str
    track: str
    stage: str
    input_fingerprint: str
    status: str
    output_refs: tuple[str, ...]
    result_checksum: str | None
    private_result: dict[str, Any]
    completed_by_attempt_id: str | None
    created_at: datetime
    updated_at: datetime


class SQLiteMarketingAnalysisRepository:
    """Persist immutable Task 3.1 identities and fenced analysis execution."""

    def __init__(
        self,
        db_path: str,
        *,
        read_transaction_connection: sqlite3.Connection | None = None,
        bootstrap_schema: bool = True,
    ) -> None:
        self._db_path = db_path
        self._read_transaction_connection = read_transaction_connection
        if read_transaction_connection is None and bootstrap_schema:
            bootstrap_content_research_schema(db_path)

    def _connect(self) -> sqlite3.Connection:
        if self._read_transaction_connection is not None:
            return _BorrowedSQLiteConnection(self._read_transaction_connection)  # type: ignore[return-value]
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def freeze_evidence_snapshot(
        self,
        *,
        workflow_run_id: str,
        scope_contract_id: str,
        retrieval_execution_unit_id: str,
        retrieval_attempt_no: int,
        query_groups: Sequence[dict[str, Any]],
        notes: Sequence[FrozenEvidenceNoteInput],
    ) -> EvidenceSnapshot:
        _required(workflow_run_id, scope_contract_id, retrieval_execution_unit_id)
        if retrieval_attempt_no < 1:
            raise ValueError("retrieval attempt number must be positive")
        frozen_query_groups = tuple(dict(group) for group in query_groups)
        query_group_ids = tuple(str(group.get("id") or "") for group in frozen_query_groups)
        if not query_group_ids or any(not value for value in query_group_ids):
            raise ValueError("evidence snapshot requires identified query groups")
        if len(set(query_group_ids)) != len(query_group_ids):
            raise ValueError("evidence snapshot query group ids must be unique")
        if len({note.note_id for note in notes}) != len(notes):
            raise ValueError("evidence snapshot note ids must be unique")
        allowed_query_ids = set(query_group_ids)
        if any(not set(note.query_provenance) <= allowed_query_ids for note in notes):
            raise ValueError("evidence note query provenance is outside the frozen query groups")

        frozen_notes = tuple(
            FrozenEvidenceNote(
                note_id=note.note_id,
                account_id=note.account_id,
                title=note.title,
                body=note.body,
                title_hash=_sha256_text(note.title),
                body_hash=_sha256_text(note.body),
                source_url=note.source_url,
                captured_at=note.captured_at,
                query_provenance=note.query_provenance,
            )
            for note in sorted(notes, key=lambda item: item.note_id)
        )
        fingerprint_payload = {
            "schema_version": EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
            "workflow_run_id": workflow_run_id,
            "scope_contract_id": scope_contract_id,
            "retrieval_execution_unit_id": retrieval_execution_unit_id,
            "retrieval_attempt_no": retrieval_attempt_no,
            "query_groups": frozen_query_groups,
            "notes": [
                {
                    "note_id": note.note_id,
                    "account_id": note.account_id,
                    "title": note.title,
                    "body": note.body,
                    "title_hash": note.title_hash,
                    "body_hash": note.body_hash,
                    "source_url": note.source_url,
                    "captured_at": note.captured_at.isoformat(),
                    "query_provenance": note.query_provenance,
                }
                for note in frozen_notes
            ],
        }
        snapshot_fingerprint = _sha256_text(_canonical_json(fingerprint_payload))
        snapshot_id = _stable_id(
            "evs",
            retrieval_execution_unit_id,
            str(retrieval_attempt_no),
        )
        created_at = datetime.now(timezone.utc)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id, snapshot_fingerprint FROM content_research_evidence_snapshots "
                "WHERE retrieval_execution_unit_id=? AND retrieval_attempt_no=?",
                (retrieval_execution_unit_id, retrieval_attempt_no),
            ).fetchone()
            if existing is not None:
                if existing["snapshot_fingerprint"] != snapshot_fingerprint:
                    raise AnalysisIdentityConflictError(
                        "retrieval attempt already froze a different evidence snapshot"
                    )
                return self._load_evidence_snapshot(conn, str(existing["id"]))
            conn.execute(
                "INSERT INTO content_research_evidence_snapshots "
                "(id, schema_version, workflow_run_id, scope_contract_id, "
                "retrieval_execution_unit_id, retrieval_attempt_no, snapshot_fingerprint, "
                "query_groups_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
                    workflow_run_id,
                    scope_contract_id,
                    retrieval_execution_unit_id,
                    retrieval_attempt_no,
                    snapshot_fingerprint,
                    _canonical_json(frozen_query_groups),
                    created_at.isoformat(),
                ),
            )
            conn.executemany(
                "INSERT INTO content_research_evidence_snapshot_notes "
                "(snapshot_id, note_id, account_id, title, body, title_hash, body_hash, "
                "source_url, captured_at, query_provenance_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id,
                        note.note_id,
                        note.account_id,
                        note.title,
                        note.body,
                        note.title_hash,
                        note.body_hash,
                        note.source_url,
                        note.captured_at.isoformat(),
                        _canonical_json(note.query_provenance),
                    )
                    for note in frozen_notes
                ],
            )
            return self._load_evidence_snapshot(conn, snapshot_id)

    def get_evidence_snapshot(self, snapshot_id: str) -> EvidenceSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM content_research_evidence_snapshots WHERE id=?",
                (snapshot_id,),
            ).fetchone()
            if row is None:
                return None
            return self._load_evidence_snapshot(conn, snapshot_id)

    def get_or_create_analysis_unit(
        self,
        *,
        evidence_snapshot_id: str,
        policy_version: str,
        prompt_hash: str,
        response_schema_hash: str,
        embedding_fingerprint: dict[str, Any],
        algorithm_version: str,
        verifier_version: str,
    ) -> AnalysisUnit:
        _required(
            evidence_snapshot_id,
            policy_version,
            prompt_hash,
            response_schema_hash,
            algorithm_version,
            verifier_version,
        )
        embedding_json = _canonical_json(embedding_fingerprint)
        contract_payload = {
            "policy_version": policy_version,
            "prompt_hash": prompt_hash,
            "response_schema_hash": response_schema_hash,
            "embedding_fingerprint": embedding_fingerprint,
            "algorithm_version": algorithm_version,
            "verifier_version": verifier_version,
        }
        contract_fingerprint = _sha256_text(_canonical_json(contract_payload))
        unit_id = _stable_id("anu", evidence_snapshot_id, contract_fingerprint)
        created_at = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = conn.execute(
                "SELECT workflow_run_id FROM content_research_evidence_snapshots WHERE id=?",
                (evidence_snapshot_id,),
            ).fetchone()
            if snapshot is None:
                raise ValueError("analysis unit evidence snapshot does not exist")
            existing = conn.execute(
                "SELECT id FROM content_research_analysis_units "
                "WHERE evidence_snapshot_id=? AND contract_fingerprint=?",
                (evidence_snapshot_id, contract_fingerprint),
            ).fetchone()
            if existing is not None:
                return self._load_analysis_unit(conn, str(existing["id"]))
            conn.execute(
                "INSERT INTO content_research_analysis_units "
                "(id, schema_version, workflow_run_id, evidence_snapshot_id, "
                "contract_fingerprint, policy_version, prompt_hash, response_schema_hash, "
                "embedding_fingerprint_json, algorithm_version, verifier_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    unit_id,
                    ANALYSIS_UNIT_SCHEMA_VERSION,
                    str(snapshot["workflow_run_id"]),
                    evidence_snapshot_id,
                    contract_fingerprint,
                    policy_version,
                    prompt_hash,
                    response_schema_hash,
                    embedding_json,
                    algorithm_version,
                    verifier_version,
                    created_at.isoformat(),
                ),
            )
            return self._load_analysis_unit(conn, unit_id)

    def create_analysis_attempt(
        self,
        analysis_unit_id: str,
        *,
        successor_of_attempt_id: str | None = None,
    ) -> AnalysisAttempt:
        _required(analysis_unit_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute(
                    "SELECT 1 FROM content_research_analysis_units WHERE id=?",
                    (analysis_unit_id,),
                ).fetchone()
                is None
            ):
                raise ValueError("analysis unit does not exist")
            if successor_of_attempt_id is not None:
                existing_successor = conn.execute(
                    "SELECT * FROM content_research_analysis_attempts "
                    "WHERE successor_of_attempt_id=?",
                    (successor_of_attempt_id,),
                ).fetchone()
                if existing_successor is not None:
                    if existing_successor["analysis_unit_id"] != analysis_unit_id:
                        raise AnalysisIdentityConflictError(
                            "analysis attempt successor belongs to another unit"
                        )
                    return self._row_to_analysis_attempt(existing_successor)
            active = conn.execute(
                "SELECT id FROM content_research_analysis_attempts "
                "WHERE analysis_unit_id=? AND state IN ('queued', 'running')",
                (analysis_unit_id,),
            ).fetchone()
            if active is not None:
                raise AnalysisActiveAttemptConflictError(
                    "analysis unit already has an active analysis attempt"
                )
            latest = conn.execute(
                "SELECT * FROM content_research_analysis_attempts "
                "WHERE analysis_unit_id=? ORDER BY attempt_no DESC LIMIT 1",
                (analysis_unit_id,),
            ).fetchone()
            if latest is None:
                if successor_of_attempt_id is not None:
                    raise AnalysisIdentityConflictError(
                        "initial analysis attempt cannot have a predecessor"
                    )
                attempt_no = 1
            else:
                if successor_of_attempt_id is None:
                    raise AnalysisIdentityConflictError(
                        "successor analysis attempt requires its predecessor"
                    )
                if latest["id"] != successor_of_attempt_id:
                    raise AnalysisIdentityConflictError(
                        "analysis successor must target the current predecessor"
                    )
                if latest["state"] not in {"failed", "cancelled"}:
                    raise AnalysisIdentityConflictError(
                        "analysis successor requires a failed predecessor"
                    )
                attempt_no = int(latest["attempt_no"]) + 1
            attempt_id = _stable_id("ana", analysis_unit_id, str(attempt_no))
            created_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO content_research_analysis_attempts "
                "(id, analysis_unit_id, attempt_no, state, successor_of_attempt_id, "
                "created_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                (
                    attempt_id,
                    analysis_unit_id,
                    attempt_no,
                    successor_of_attempt_id,
                    created_at,
                ),
            )
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is not None
            ):
                conn.execute(
                    "UPDATE workflow_runs SET effective_analysis_attempt_id=? WHERE run_id=("
                    "SELECT workflow_run_id FROM content_research_analysis_units WHERE id=?"
                    ")",
                    (attempt_id, analysis_unit_id),
                )
            return self._load_analysis_attempt(conn, attempt_id)

    def save_analysis_job_context(
        self,
        *,
        analysis_unit_id: str,
        workflow_run_id: str,
        research_plan_id: str,
        coverage_snapshot_id: str,
        execution_authorization_id: str | None,
        manifest: dict[str, Any],
    ) -> AnalysisJobContext:
        _required(
            analysis_unit_id,
            workflow_run_id,
            research_plan_id,
            coverage_snapshot_id,
        )
        manifest_json = _canonical_json(manifest)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            unit = conn.execute(
                "SELECT workflow_run_id FROM content_research_analysis_units WHERE id=?",
                (analysis_unit_id,),
            ).fetchone()
            if unit is None:
                raise ValueError("analysis job unit does not exist")
            if str(unit["workflow_run_id"]) != workflow_run_id:
                raise AnalysisIdentityConflictError("analysis job workflow does not match its unit")
            existing = conn.execute(
                "SELECT * FROM content_research_analysis_jobs WHERE analysis_unit_id=?",
                (analysis_unit_id,),
            ).fetchone()
            if existing is not None:
                same = (
                    str(existing["workflow_run_id"]) == workflow_run_id
                    and str(existing["research_plan_id"]) == research_plan_id
                    and str(existing["coverage_snapshot_id"]) == coverage_snapshot_id
                    and existing["execution_authorization_id"] == execution_authorization_id
                    and str(existing["manifest_json"]) == manifest_json
                )
                if not same:
                    raise AnalysisIdentityConflictError("analysis job context is immutable")
                return self._row_to_analysis_job_context(existing)
            conn.execute(
                "INSERT INTO content_research_analysis_jobs "
                "(analysis_unit_id, workflow_run_id, research_plan_id, coverage_snapshot_id, "
                "execution_authorization_id, manifest_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    analysis_unit_id,
                    workflow_run_id,
                    research_plan_id,
                    coverage_snapshot_id,
                    execution_authorization_id,
                    manifest_json,
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM content_research_analysis_jobs WHERE analysis_unit_id=?",
                (analysis_unit_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_analysis_job_context(row)

    def get_analysis_job_context(self, analysis_unit_id: str) -> AnalysisJobContext | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_analysis_jobs WHERE analysis_unit_id=?",
                (analysis_unit_id,),
            ).fetchone()
        return self._row_to_analysis_job_context(row) if row is not None else None

    def claim_next_analysis_job(
        self,
        *,
        lease_owner: str,
        lease_token: str,
        lease_expires_at: datetime,
        now: datetime | None = None,
    ) -> AnalysisJobClaim | None:
        _required(lease_owner, lease_token)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or lease_expires_at.tzinfo is None:
            raise ValueError("analysis lease times must be timezone-aware")
        if lease_expires_at <= current_time:
            raise ValueError("analysis lease must expire in the future")
        with self._connect() as conn:
            has_workflow_runs = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is not None
            )
            lifecycle_join = (
                "LEFT JOIN workflow_runs AS run ON run.run_id=job.workflow_run_id "
                if has_workflow_runs
                else ""
            )
            lifecycle_filter = (
                "AND (run.run_id IS NULL OR run.content_research_state='report_composing') "
                if has_workflow_runs
                else ""
            )
            # Empty-queue polling is read-only. The analysis worker wakes on a
            # short interval even while retrieval is persisting Spider results;
            # taking a write reservation for every empty scan needlessly
            # competes with that real work and can surface as `database is
            # locked`. Re-check inside the write transaction below before
            # claiming so this preflight remains only an optimization.
            preflight = conn.execute(
                "SELECT 1 FROM content_research_analysis_attempts AS attempt "
                "JOIN content_research_analysis_jobs AS job "
                "ON job.analysis_unit_id=attempt.analysis_unit_id "
                + lifecycle_join
                + "WHERE attempt.state='queued' "
                + lifecycle_filter
                + "LIMIT 1"
            ).fetchone()
            if preflight is None:
                return None
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempt.id FROM content_research_analysis_attempts AS attempt "
                "JOIN content_research_analysis_jobs AS job "
                "ON job.analysis_unit_id=attempt.analysis_unit_id "
                + lifecycle_join
                + "WHERE attempt.state='queued' "
                + lifecycle_filter
                + "ORDER BY attempt.created_at, attempt.id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            attempt_id = str(row["id"])
            updated = conn.execute(
                "UPDATE content_research_analysis_attempts SET state='running', "
                "lease_owner=?, lease_token=?, lease_expires_at=? "
                "WHERE id=? AND state='queued'",
                (lease_owner, lease_token, lease_expires_at.isoformat(), attempt_id),
            )
            if updated.rowcount != 1:
                return None
            attempt = self._load_analysis_attempt(conn, attempt_id)
            context_row = conn.execute(
                "SELECT * FROM content_research_analysis_jobs WHERE analysis_unit_id=?",
                (attempt.analysis_unit_id,),
            ).fetchone()
            assert context_row is not None
            return AnalysisJobClaim(
                context=self._row_to_analysis_job_context(context_row),
                attempt=attempt,
            )

    def recover_expired_analysis_jobs(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[AnalysisAttempt, ...]:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("analysis lease check time must be timezone-aware")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            has_workflow_runs = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is not None
            )
            lifecycle_join = (
                "LEFT JOIN workflow_runs AS run ON run.run_id=job.workflow_run_id "
                if has_workflow_runs
                else ""
            )
            lifecycle_filter = (
                "AND (run.run_id IS NULL OR run.content_research_state='report_composing') "
                if has_workflow_runs
                else ""
            )
            rows = conn.execute(
                "SELECT attempt.* FROM content_research_analysis_attempts AS attempt "
                "JOIN content_research_analysis_jobs AS job "
                "ON job.analysis_unit_id=attempt.analysis_unit_id "
                + lifecycle_join
                + "WHERE attempt.state='running' AND attempt.lease_expires_at IS NOT NULL "
                + lifecycle_filter
                + "AND attempt.lease_expires_at<=? ORDER BY attempt.created_at, attempt.id",
                (current_time.isoformat(),),
            ).fetchall()
            expired = tuple(self._row_to_analysis_attempt(row) for row in rows)
            for attempt in expired:
                conn.execute(
                    "UPDATE content_research_analysis_attempts "
                    "SET state='failed', terminal_at=? WHERE id=? AND state='running'",
                    (current_time.isoformat(), attempt.id),
                )
            return expired

    def list_expired_analysis_jobs(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[AnalysisAttempt, ...]:
        """Read expired authoritative jobs; the lifecycle coordinator closes them."""
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("analysis lease check time must be timezone-aware")
        with self._connect() as conn:
            has_workflow_runs = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is not None
            )
            if not has_workflow_runs:
                return ()
            rows = conn.execute(
                "SELECT attempt.* FROM workflow_runs AS run "
                "JOIN content_research_analysis_attempts AS attempt "
                "ON attempt.id=run.effective_analysis_attempt_id "
                "JOIN content_research_analysis_units AS unit "
                "ON unit.id=attempt.analysis_unit_id AND unit.workflow_run_id=run.run_id "
                "WHERE attempt.state='running' "
                "AND attempt.lease_expires_at IS NOT NULL "
                "AND attempt.lease_expires_at<=? "
                "ORDER BY attempt.created_at, attempt.id",
                (current_time.isoformat(),),
            ).fetchall()
        return tuple(self._row_to_analysis_attempt(row) for row in rows)

    def claim_analysis_attempt(
        self,
        attempt_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_expires_at: datetime,
        now: datetime | None = None,
    ) -> AnalysisAttempt:
        _required(attempt_id, lease_owner, lease_token)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or lease_expires_at.tzinfo is None:
            raise ValueError("analysis lease times must be timezone-aware")
        if lease_expires_at <= current_time:
            raise ValueError("analysis lease must expire in the future")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM content_research_analysis_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("analysis attempt does not exist")
            if row["state"] == "running" and row["lease_token"] == lease_token:
                return self._row_to_analysis_attempt(row)
            if row["state"] != "queued":
                raise AnalysisLeaseFencedError("analysis attempt cannot be claimed")
            conn.execute(
                "UPDATE content_research_analysis_attempts SET state='running', "
                "lease_owner=?, lease_token=?, lease_expires_at=? WHERE id=? AND state='queued'",
                (lease_owner, lease_token, lease_expires_at.isoformat(), attempt_id),
            )
            return self._load_analysis_attempt(conn, attempt_id)

    def fail_analysis_attempt(
        self,
        attempt_id: str,
        *,
        lease_token: str,
        now: datetime | None = None,
    ) -> AnalysisAttempt:
        current_time = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            conn.execute(
                "UPDATE content_research_analysis_attempts "
                "SET state='failed', terminal_at=? WHERE id=?",
                (current_time.isoformat(), row["id"]),
            )
            return self._load_analysis_attempt(conn, attempt_id)

    def succeed_analysis_attempt(
        self,
        attempt_id: str,
        *,
        lease_token: str,
        now: datetime | None = None,
    ) -> AnalysisAttempt:
        current_time = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            completed_tracks = {
                str(item["track"])
                for item in conn.execute(
                    "SELECT track FROM content_research_analysis_checkpoints "
                    "WHERE analysis_unit_id=? AND stage='verifier' AND status='completed'",
                    (row["analysis_unit_id"],),
                ).fetchall()
            }
            if completed_tracks != {"need", "value", "message"}:
                raise AnalysisIdentityConflictError(
                    "analysis attempt cannot succeed before every planned track completes"
                )
            conn.execute(
                "UPDATE content_research_analysis_attempts "
                "SET state='succeeded', terminal_at=? WHERE id=?",
                (current_time.isoformat(), row["id"]),
            )
            return self._load_analysis_attempt(conn, attempt_id)

    def succeed_analysis_attempt_with_checkpoint(
        self,
        attempt_id: str,
        *,
        lease_token: str,
        checkpoint: StageCheckpointRecord,
        now: datetime | None = None,
    ) -> tuple[AnalysisAttempt, StageCheckpointRecord]:
        """Commit track coverage, attempt success, and Run pointer together."""
        current_time = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            completed_tracks = {
                str(item["track"])
                for item in conn.execute(
                    "SELECT track FROM content_research_analysis_checkpoints "
                    "WHERE analysis_unit_id=? AND stage='verifier' AND status='completed'",
                    (row["analysis_unit_id"],),
                ).fetchall()
            }
            if completed_tracks != {"need", "value", "message"}:
                raise AnalysisIdentityConflictError(
                    "analysis attempt cannot succeed before every planned track completes"
                )
            if (
                checkpoint.workflow_run_id
                != conn.execute(
                    "SELECT workflow_run_id FROM content_research_analysis_units WHERE id=?",
                    (row["analysis_unit_id"],),
                ).fetchone()[0]
            ):
                raise AnalysisIdentityConflictError(
                    "analysis terminal checkpoint belongs to another Run"
                )
            values = (
                checkpoint.id,
                checkpoint.schema_version,
                checkpoint.workflow_run_id,
                checkpoint.subagent_task_id,
                checkpoint.stage_name,
                checkpoint.input_fingerprint,
                checkpoint.status,
                checkpoint.retry_count,
                checkpoint.started_at.isoformat() if checkpoint.started_at else None,
                checkpoint.finished_at.isoformat() if checkpoint.finished_at else None,
                checkpoint.scope_contract_id,
                checkpoint.execution_unit_id,
                checkpoint.attempt_no,
                checkpoint.execution_revision,
                _canonical_json(checkpoint.payload),
                _canonical_json(checkpoint.metadata),
                checkpoint.created_at.isoformat(),
            )
            conn.execute(
                "INSERT INTO content_research_stage_checkpoints "
                "(id, schema_version, workflow_run_id, subagent_task_id, stage_name, "
                "input_fingerprint, status, retry_count, started_at, finished_at, "
                "scope_contract_id, execution_unit_id, attempt_no, execution_revision, "
                "payload_json, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            conn.execute(
                "UPDATE content_research_analysis_attempts "
                "SET state='succeeded', terminal_at=?, lease_expires_at=NULL WHERE id=?",
                (current_time.isoformat(), attempt_id),
            )
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is not None
            ):
                conn.execute(
                    "UPDATE workflow_runs SET effective_analysis_attempt_id=? WHERE run_id=?",
                    (attempt_id, checkpoint.workflow_run_id),
                )
            return self._load_analysis_attempt(conn, attempt_id), checkpoint

    def renew_analysis_attempt(
        self,
        attempt_id: str,
        *,
        lease_token: str,
        lease_expires_at: datetime,
        now: datetime | None = None,
    ) -> AnalysisAttempt:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or lease_expires_at.tzinfo is None:
            raise ValueError("analysis lease times must be timezone-aware")
        if lease_expires_at <= current_time:
            raise ValueError("analysis lease must expire in the future")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            conn.execute(
                "UPDATE content_research_analysis_attempts SET lease_expires_at=? WHERE id=?",
                (lease_expires_at.isoformat(), row["id"]),
            )
            return self._load_analysis_attempt(conn, attempt_id)

    def expire_analysis_attempts(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[AnalysisAttempt, ...]:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("analysis lease check time must be timezone-aware")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id FROM content_research_analysis_attempts "
                "WHERE state='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<=? "
                "ORDER BY created_at, id",
                (current_time.isoformat(),),
            ).fetchall()
            attempt_ids = tuple(str(row["id"]) for row in rows)
            if attempt_ids:
                conn.executemany(
                    "UPDATE content_research_analysis_attempts "
                    "SET state='failed', terminal_at=? WHERE id=? AND state='running'",
                    [(current_time.isoformat(), attempt_id) for attempt_id in attempt_ids],
                )
            return tuple(
                self._load_analysis_attempt(conn, attempt_id) for attempt_id in attempt_ids
            )

    def get_analysis_attempt(self, attempt_id: str) -> AnalysisAttempt | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_analysis_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
        return self._row_to_analysis_attempt(row) if row is not None else None

    def get_analysis_unit(self, analysis_unit_id: str) -> AnalysisUnit | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM content_research_analysis_units WHERE id=?",
                (analysis_unit_id,),
            ).fetchone()
            if row is None:
                return None
            return self._load_analysis_unit(conn, analysis_unit_id)

    def get_effective_attempt_for_run(self, workflow_run_id: str) -> AnalysisAttempt | None:
        with self._connect() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is None
            ):
                return None
            row = conn.execute(
                "SELECT attempt.* FROM workflow_runs AS run "
                "JOIN content_research_analysis_attempts AS attempt "
                "ON attempt.id=run.effective_analysis_attempt_id "
                "JOIN content_research_analysis_units AS unit "
                "ON unit.id=attempt.analysis_unit_id AND unit.workflow_run_id=run.run_id "
                "WHERE run.run_id=?",
                (workflow_run_id,),
            ).fetchone()
        return self._row_to_analysis_attempt(row) if row is not None else None

    def get_latest_attempt_for_unit(self, analysis_unit_id: str) -> AnalysisAttempt | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_analysis_attempts "
                "WHERE analysis_unit_id=? ORDER BY attempt_no DESC LIMIT 1",
                (analysis_unit_id,),
            ).fetchone()
        return self._row_to_analysis_attempt(row) if row is not None else None

    def complete_analysis_checkpoint(
        self,
        *,
        analysis_unit_id: str,
        attempt_id: str,
        lease_token: str,
        track: str,
        stage: str,
        input_fingerprint: str,
        output_refs: Sequence[str],
        result_checksum: str,
        private_result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AnalysisCheckpoint:
        _required(
            analysis_unit_id,
            attempt_id,
            lease_token,
            track,
            stage,
            input_fingerprint,
            result_checksum,
        )
        if track not in {"shared", "need", "value", "message"}:
            raise ValueError("invalid analysis checkpoint track")
        current_time = now or datetime.now(timezone.utc)
        checkpoint_id = _stable_id(
            "anc",
            analysis_unit_id,
            track,
            stage,
            input_fingerprint,
        )
        output_refs_tuple = tuple(output_refs)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            if attempt["analysis_unit_id"] != analysis_unit_id:
                raise AnalysisLeaseFencedError(
                    "attempt is not the active lease attempt for this unit"
                )
            existing = conn.execute(
                "SELECT * FROM content_research_analysis_checkpoints WHERE id=?",
                (checkpoint_id,),
            ).fetchone()
            if existing is not None:
                same_result = (
                    tuple(json.loads(existing["output_refs_json"])) == output_refs_tuple
                    and existing["result_checksum"] == result_checksum
                    and dict(json.loads(existing["private_result_json"] or "{}"))
                    == (private_result or {})
                    and existing["status"] == "completed"
                )
                if not same_result:
                    raise AnalysisIdentityConflictError(
                        "completed analysis checkpoint is immutable"
                    )
                return self._row_to_analysis_checkpoint(existing)
            timestamp = current_time.isoformat()
            conn.execute(
                "INSERT INTO content_research_analysis_checkpoints "
                "(id, analysis_unit_id, track, stage, input_fingerprint, status, "
                "output_refs_json, result_checksum, private_result_json, "
                "completed_by_attempt_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    analysis_unit_id,
                    track,
                    stage,
                    input_fingerprint,
                    _canonical_json(output_refs_tuple),
                    result_checksum,
                    _canonical_json(private_result or {}),
                    attempt_id,
                    timestamp,
                    timestamp,
                ),
            )
            return self._load_analysis_checkpoint(conn, checkpoint_id)

    def fail_analysis_checkpoint(
        self,
        *,
        analysis_unit_id: str,
        attempt_id: str,
        lease_token: str,
        track: str,
        stage: str,
        input_fingerprint: str,
        error_code: str,
        private_result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AnalysisCheckpoint:
        """Persist one attempt-scoped safe failure without poisoning reusable success."""
        _required(
            analysis_unit_id,
            attempt_id,
            lease_token,
            track,
            stage,
            input_fingerprint,
            error_code,
        )
        if track not in {"shared", "need", "value", "message"}:
            raise ValueError("invalid analysis checkpoint track")
        current_time = now or datetime.now(timezone.utc)
        checkpoint_id = _stable_id(
            "anf",
            analysis_unit_id,
            attempt_id,
            track,
            stage,
            input_fingerprint,
        )
        safe_result = {**(private_result or {}), "error_code": error_code}
        checksum = _sha256_text(_canonical_json(safe_result))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            if attempt["analysis_unit_id"] != analysis_unit_id:
                raise AnalysisLeaseFencedError(
                    "attempt is not the active lease attempt for this unit"
                )
            existing = conn.execute(
                "SELECT * FROM content_research_analysis_checkpoints WHERE id=?",
                (checkpoint_id,),
            ).fetchone()
            if existing is not None:
                if existing["status"] != "failed" or existing["result_checksum"] != checksum:
                    raise AnalysisIdentityConflictError("failed analysis checkpoint is immutable")
                return self._row_to_analysis_checkpoint(existing)
            timestamp = current_time.isoformat()
            conn.execute(
                "INSERT INTO content_research_analysis_checkpoints "
                "(id, analysis_unit_id, track, stage, input_fingerprint, status, "
                "output_refs_json, result_checksum, private_result_json, "
                "completed_by_attempt_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'failed', '[]', ?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    analysis_unit_id,
                    track,
                    stage,
                    input_fingerprint,
                    checksum,
                    _canonical_json(safe_result),
                    attempt_id,
                    timestamp,
                    timestamp,
                ),
            )
            return self._load_analysis_checkpoint(conn, checkpoint_id)

    def complete_analysis_track(
        self,
        *,
        analysis_unit_id: str,
        attempt_id: str,
        lease_token: str,
        track: str,
        input_fingerprint: str,
        candidates: Sequence[MarketingConclusionCandidateRecord],
        decision: MarketingConclusionDecisionRecord,
        result_checksum: str,
        now: datetime | None = None,
    ) -> AnalysisCheckpoint:
        """Atomically commit proposals, backend decision, and verifier checkpoint."""
        current_time = now or datetime.now(timezone.utc)
        checkpoint_id = _stable_id("anc", analysis_unit_id, track, "verifier", input_fingerprint)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = self._require_live_attempt(
                conn,
                attempt_id=attempt_id,
                lease_token=lease_token,
                now=current_time,
            )
            if attempt["analysis_unit_id"] != analysis_unit_id:
                raise AnalysisLeaseFencedError(
                    "attempt is not the active lease attempt for this unit"
                )
            if decision.track != track:
                raise AnalysisIdentityConflictError("analysis decision crossed its track boundary")
            existing_checkpoint = conn.execute(
                "SELECT * FROM content_research_analysis_checkpoints WHERE id=?",
                (checkpoint_id,),
            ).fetchone()
            if existing_checkpoint is not None:
                if (
                    existing_checkpoint["status"] != "completed"
                    or existing_checkpoint["result_checksum"] != result_checksum
                    or tuple(json.loads(existing_checkpoint["output_refs_json"])) != (decision.id,)
                ):
                    raise AnalysisIdentityConflictError("completed analysis track is immutable")
                return self._row_to_analysis_checkpoint(existing_checkpoint)
            run = (
                conn.execute(
                    "SELECT content_research_state FROM workflow_runs WHERE run_id=?",
                    (decision.workflow_run_id,),
                ).fetchone()
                if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
                ).fetchone()
                is not None
                else None
            )
            if run is not None and run["content_research_state"] == "cancelled_or_failed":
                raise AnalysisLeaseFencedError("analysis output cannot commit after cancellation")

            def insert_candidate(record: MarketingConclusionCandidateRecord) -> None:
                conn.execute(
                    "INSERT INTO content_research_marketing_conclusion_candidates "
                    "(id, schema_version, workflow_run_id, research_plan_id, track, "
                    "payload_json, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.schema_version,
                        record.workflow_run_id,
                        record.research_plan_id,
                        record.track,
                        _canonical_json(record.payload),
                        _canonical_json(record.metadata),
                        record.created_at.isoformat(),
                    ),
                )

            for candidate in candidates:
                insert_candidate(candidate)
            conn.execute(
                "INSERT INTO content_research_marketing_conclusion_decisions "
                "(id, schema_version, workflow_run_id, research_plan_id, candidate_id, "
                "track, state, payload_json, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.id,
                    decision.schema_version,
                    decision.workflow_run_id,
                    decision.research_plan_id,
                    decision.candidate_id,
                    decision.track,
                    decision.state,
                    _canonical_json(decision.payload),
                    _canonical_json(decision.metadata),
                    decision.created_at.isoformat(),
                ),
            )
            timestamp = current_time.isoformat()
            conn.execute(
                "INSERT INTO content_research_analysis_checkpoints "
                "(id, analysis_unit_id, track, stage, input_fingerprint, status, "
                "output_refs_json, result_checksum, private_result_json, "
                "completed_by_attempt_id, created_at, updated_at) "
                "VALUES (?, ?, ?, 'verifier', ?, 'completed', ?, ?, '{}', ?, ?, ?)",
                (
                    checkpoint_id,
                    analysis_unit_id,
                    track,
                    input_fingerprint,
                    _canonical_json((decision.id,)),
                    result_checksum,
                    attempt_id,
                    timestamp,
                    timestamp,
                ),
            )
            return self._load_analysis_checkpoint(conn, checkpoint_id)

    def get_completed_analysis_checkpoint(
        self,
        *,
        analysis_unit_id: str,
        track: str,
        stage: str,
        input_fingerprint: str,
    ) -> AnalysisCheckpoint | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_analysis_checkpoints "
                "WHERE analysis_unit_id=? AND track=? AND stage=? "
                "AND input_fingerprint=? AND status='completed'",
                (analysis_unit_id, track, stage, input_fingerprint),
            ).fetchone()
        return self._row_to_analysis_checkpoint(row) if row is not None else None

    def list_analysis_checkpoints(self, analysis_unit_id: str) -> tuple[AnalysisCheckpoint, ...]:
        """Return the durable unit checkpoints in their commit order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_analysis_checkpoints "
                "WHERE analysis_unit_id=? ORDER BY created_at, id",
                (analysis_unit_id,),
            ).fetchall()
        return tuple(self._row_to_analysis_checkpoint(row) for row in rows)

    @staticmethod
    def _require_live_attempt(
        conn: sqlite3.Connection,
        *,
        attempt_id: str,
        lease_token: str,
        now: datetime,
    ) -> sqlite3.Row:
        if now.tzinfo is None:
            raise ValueError("analysis lease check time must be timezone-aware")
        row = conn.execute(
            "SELECT * FROM content_research_analysis_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        expires_at = (
            datetime.fromisoformat(str(row["lease_expires_at"]))
            if row is not None and row["lease_expires_at"]
            else None
        )
        if (
            row is None
            or row["state"] != "running"
            or row["lease_token"] != lease_token
            or expires_at is None
            or expires_at <= now
        ):
            raise AnalysisLeaseFencedError("attempt is not the active lease attempt")
        active = conn.execute(
            "SELECT id FROM content_research_analysis_attempts "
            "WHERE analysis_unit_id=? AND state='running'",
            (row["analysis_unit_id"],),
        ).fetchone()
        if active is None or active["id"] != attempt_id:
            raise AnalysisLeaseFencedError("attempt is not the active lease attempt")
        return row

    @staticmethod
    def _load_analysis_unit(conn: sqlite3.Connection, unit_id: str) -> AnalysisUnit:
        row = conn.execute(
            "SELECT * FROM content_research_analysis_units WHERE id=?",
            (unit_id,),
        ).fetchone()
        if row is None:
            raise LookupError("analysis unit does not exist")
        return AnalysisUnit(
            id=str(row["id"]),
            schema_version=str(row["schema_version"]),
            workflow_run_id=str(row["workflow_run_id"]),
            evidence_snapshot_id=str(row["evidence_snapshot_id"]),
            contract_fingerprint=str(row["contract_fingerprint"]),
            policy_version=str(row["policy_version"]),
            prompt_hash=str(row["prompt_hash"]),
            response_schema_hash=str(row["response_schema_hash"]),
            embedding_fingerprint=dict(json.loads(row["embedding_fingerprint_json"])),
            algorithm_version=str(row["algorithm_version"]),
            verifier_version=str(row["verifier_version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _row_to_analysis_job_context(row: sqlite3.Row) -> AnalysisJobContext:
        return AnalysisJobContext(
            analysis_unit_id=str(row["analysis_unit_id"]),
            workflow_run_id=str(row["workflow_run_id"]),
            research_plan_id=str(row["research_plan_id"]),
            coverage_snapshot_id=str(row["coverage_snapshot_id"]),
            execution_authorization_id=(
                str(row["execution_authorization_id"])
                if row["execution_authorization_id"] is not None
                else None
            ),
            manifest=dict(json.loads(str(row["manifest_json"]))),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _row_to_analysis_attempt(row: sqlite3.Row) -> AnalysisAttempt:
        return AnalysisAttempt(
            id=str(row["id"]),
            analysis_unit_id=str(row["analysis_unit_id"]),
            attempt_no=int(row["attempt_no"]),
            state=str(row["state"]),
            successor_of_attempt_id=(
                str(row["successor_of_attempt_id"])
                if row["successor_of_attempt_id"] is not None
                else None
            ),
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_token=str(row["lease_token"]) if row["lease_token"] is not None else None,
            lease_expires_at=(
                datetime.fromisoformat(str(row["lease_expires_at"]))
                if row["lease_expires_at"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            terminal_at=(
                datetime.fromisoformat(str(row["terminal_at"]))
                if row["terminal_at"] is not None
                else None
            ),
        )

    @classmethod
    def _load_analysis_attempt(
        cls,
        conn: sqlite3.Connection,
        attempt_id: str,
    ) -> AnalysisAttempt:
        row = conn.execute(
            "SELECT * FROM content_research_analysis_attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise LookupError("analysis attempt does not exist")
        return cls._row_to_analysis_attempt(row)

    @staticmethod
    def _row_to_analysis_checkpoint(row: sqlite3.Row) -> AnalysisCheckpoint:
        return AnalysisCheckpoint(
            id=str(row["id"]),
            analysis_unit_id=str(row["analysis_unit_id"]),
            track=str(row["track"]),
            stage=str(row["stage"]),
            input_fingerprint=str(row["input_fingerprint"]),
            status=str(row["status"]),
            output_refs=tuple(json.loads(row["output_refs_json"])),
            result_checksum=(
                str(row["result_checksum"]) if row["result_checksum"] is not None else None
            ),
            private_result=dict(json.loads(str(row["private_result_json"] or "{}"))),
            completed_by_attempt_id=(
                str(row["completed_by_attempt_id"])
                if row["completed_by_attempt_id"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @classmethod
    def _load_analysis_checkpoint(
        cls,
        conn: sqlite3.Connection,
        checkpoint_id: str,
    ) -> AnalysisCheckpoint:
        row = conn.execute(
            "SELECT * FROM content_research_analysis_checkpoints WHERE id=?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            raise LookupError("analysis checkpoint does not exist")
        return cls._row_to_analysis_checkpoint(row)

    @staticmethod
    def _load_evidence_snapshot(
        conn: sqlite3.Connection,
        snapshot_id: str,
    ) -> EvidenceSnapshot:
        row = conn.execute(
            "SELECT * FROM content_research_evidence_snapshots WHERE id=?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise LookupError("evidence snapshot does not exist")
        note_rows = conn.execute(
            "SELECT * FROM content_research_evidence_snapshot_notes "
            "WHERE snapshot_id=? ORDER BY note_id",
            (snapshot_id,),
        ).fetchall()
        return EvidenceSnapshot(
            id=str(row["id"]),
            schema_version=str(row["schema_version"]),
            workflow_run_id=str(row["workflow_run_id"]),
            scope_contract_id=str(row["scope_contract_id"]),
            retrieval_execution_unit_id=str(row["retrieval_execution_unit_id"]),
            retrieval_attempt_no=int(row["retrieval_attempt_no"]),
            snapshot_fingerprint=str(row["snapshot_fingerprint"]),
            query_groups=tuple(dict(group) for group in json.loads(row["query_groups_json"])),
            notes=tuple(
                FrozenEvidenceNote(
                    note_id=str(note["note_id"]),
                    account_id=str(note["account_id"]),
                    title=str(note["title"]),
                    body=str(note["body"]),
                    title_hash=str(note["title_hash"]),
                    body_hash=str(note["body_hash"]),
                    source_url=str(note["source_url"]),
                    captured_at=datetime.fromisoformat(str(note["captured_at"])),
                    query_provenance=tuple(json.loads(note["query_provenance_json"])),
                )
                for note in note_rows
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
