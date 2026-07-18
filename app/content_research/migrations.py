"""Append-only SQLite migrations for the Content Research schema.

Migration bodies are deliberately immutable.  Once a version is published its
SQL/operation definition must never be edited: evolve the schema with a new
version instead.  F003 is not released yet, so 0005 may intentionally discard
the temporary generic-role records created by the early refactor.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

_TYPED_TABLES = (
    "content_research_canonical_sources",
    "content_research_direction_source_projections",
    "content_research_directional_evidence_packets",
    "content_research_claim_candidates",
    "content_research_claim_admission_decisions",
    "content_research_direction_result_decisions",
    "content_research_weak_signals",
    "content_research_cross_direction_records",
    "content_research_aggregate_claims",
    "content_research_stage_checkpoints",
    "content_research_budget_ledger_entries",
    "content_research_report_faithfulness_decisions",
)


def _migration_0002_sql() -> str:
    """Frozen first formal-schema migration, including its original generic fields."""
    statements = [
        "CREATE TABLE IF NOT EXISTS content_research_run_policy_snapshots (id TEXT PRIMARY KEY, workflow_run_id TEXT NOT NULL UNIQUE, research_brief_id TEXT NOT NULL, research_plan_id TEXT NOT NULL, schema_version TEXT NOT NULL, effective_policy_json TEXT NOT NULL, effective_policy_hash TEXT NOT NULL, run_as_of_at TEXT NOT NULL, base_policy_json TEXT NOT NULL DEFAULT '{}', requested_overrides_json TEXT NOT NULL DEFAULT '{}', validation_result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}')",
        "CREATE TABLE IF NOT EXISTS content_research_sample_policies (id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, direction_id TEXT NOT NULL, minimum_samples INTEGER NOT NULL, minimum_independent_authors INTEGER NOT NULL, author_cap INTEGER NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}')",
        "CREATE TABLE IF NOT EXISTS content_research_direction_contracts (id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, direction_id TEXT NOT NULL, schema_version TEXT NOT NULL, sample_policy_id TEXT NOT NULL, required_note_fields_json TEXT NOT NULL, optional_note_fields_json TEXT NOT NULL DEFAULT '[]', required_comment_fields_json TEXT NOT NULL DEFAULT '[]', claim_rules_json TEXT NOT NULL DEFAULT '[]', analysis_schema_version TEXT NOT NULL, resume_contract_version TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', UNIQUE(snapshot_id, direction_id))",
        "CREATE INDEX IF NOT EXISTS idx_cr_snapshot_workflow ON content_research_run_policy_snapshots(workflow_run_id)",
        "CREATE INDEX IF NOT EXISTS idx_cr_contract_snapshot ON content_research_direction_contracts(snapshot_id)",
    ]
    for table in _TYPED_TABLES:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, relation_a TEXT, relation_b TEXT, state TEXT, payload_json TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{{}}', created_at TEXT NOT NULL)"
        )
    return ";\n".join(statements) + ";"


# Frozen definition of migration 0003.  Do not add fields here.
_V3_COLUMNS = {
    "content_research_canonical_sources": ("platform TEXT", "platform_source_kind TEXT", "platform_source_id TEXT", "canonical_url TEXT"),
    "content_research_direction_source_projections": ("research_direction_id TEXT", "canonical_source_id TEXT", "evidence_packet_id TEXT", "eligibility_state TEXT"),
    "content_research_directional_evidence_packets": ("research_direction_id TEXT", "canonical_source_id TEXT", "field_projection_hash TEXT"),
    "content_research_claim_candidates": ("research_direction_id TEXT", "evidence_packet_id TEXT", "statement TEXT"),
    "content_research_claim_admission_decisions": ("research_direction_id TEXT", "claim_candidate_id TEXT", "decision TEXT", "policy_snapshot_id TEXT"),
    "content_research_stage_checkpoints": ("subagent_task_id TEXT", "stage_name TEXT", "input_fingerprint TEXT"),
    "content_research_budget_ledger_entries": ("research_plan_id TEXT", "idempotency_key TEXT", "reservation_status TEXT"),
}

# Fields discovered after 0003 are a new migration, never a rewrite of 0003.
_V4_COLUMNS = {
    "content_research_direction_result_decisions": ("research_direction_id TEXT", "policy_snapshot_id TEXT"),
    "content_research_weak_signals": ("admission_decision_id TEXT",),
    "content_research_cross_direction_records": ("research_plan_id TEXT", "record_type TEXT"),
    "content_research_aggregate_claims": ("research_plan_id TEXT", "aggregate_type TEXT"),
    "content_research_stage_checkpoints": ("status TEXT", "retry_count INTEGER NOT NULL DEFAULT 0"),
    "content_research_budget_ledger_entries": ("research_direction_id TEXT", "reserved_amount REAL", "consumed_amount REAL", "stage_checkpoint_id TEXT"),
    "content_research_report_faithfulness_decisions": ("research_plan_id TEXT", "result_snapshot_id TEXT"),
}


def _add_columns(conn: sqlite3.Connection, columns_by_table: dict[str, tuple[str, ...]]) -> None:
    for table, columns in columns_by_table.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column.split()[0] not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")


def _apply_0003(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V3_COLUMNS)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_ledger_idempotency ON content_research_budget_ledger_entries(idempotency_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_checkpoint_task_stage ON content_research_stage_checkpoints(subagent_task_id, stage_name)")


def _apply_0004(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V4_COLUMNS)


def _final_typed_table_sql(table: str) -> str:
    columns = {
        "content_research_canonical_sources": "platform TEXT NOT NULL, platform_source_kind TEXT NOT NULL, platform_source_id TEXT NOT NULL, canonical_url TEXT",
        "content_research_direction_source_projections": "research_direction_id TEXT NOT NULL, canonical_source_id TEXT NOT NULL, evidence_packet_id TEXT NOT NULL, eligibility_state TEXT",
        "content_research_directional_evidence_packets": "research_direction_id TEXT NOT NULL, canonical_source_id TEXT NOT NULL, field_projection_hash TEXT NOT NULL",
        "content_research_claim_candidates": "research_direction_id TEXT NOT NULL, evidence_packet_id TEXT NOT NULL, statement TEXT NOT NULL",
        "content_research_claim_admission_decisions": "research_direction_id TEXT NOT NULL, claim_candidate_id TEXT NOT NULL, decision TEXT NOT NULL, policy_snapshot_id TEXT",
        "content_research_direction_result_decisions": "research_direction_id TEXT NOT NULL, policy_snapshot_id TEXT NOT NULL",
        "content_research_weak_signals": "admission_decision_id TEXT NOT NULL",
        "content_research_cross_direction_records": "research_plan_id TEXT NOT NULL, record_type TEXT NOT NULL",
        "content_research_aggregate_claims": "research_plan_id TEXT NOT NULL, aggregate_type TEXT NOT NULL",
        "content_research_stage_checkpoints": "subagent_task_id TEXT NOT NULL, stage_name TEXT NOT NULL, input_fingerprint TEXT NOT NULL, status TEXT NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0",
        "content_research_budget_ledger_entries": "research_plan_id TEXT NOT NULL, research_direction_id TEXT, idempotency_key TEXT NOT NULL, reservation_status TEXT NOT NULL, reserved_amount REAL NOT NULL DEFAULT 0, consumed_amount REAL NOT NULL DEFAULT 0, stage_checkpoint_id TEXT",
        "content_research_report_faithfulness_decisions": "research_plan_id TEXT NOT NULL, result_snapshot_id TEXT NOT NULL",
    }[table]
    return f"CREATE TABLE {table} (id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, {columns}, payload_json TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{{}}', created_at TEXT NOT NULL)"


def _apply_0005(conn: sqlite3.Connection) -> None:
    """Replace temporary generic entities; F003 has no released data to retain."""
    for table in _TYPED_TABLES:
        conn.execute(f"DROP TABLE {table}")
        conn.execute(_final_typed_table_sql(table))
    conn.execute("CREATE UNIQUE INDEX idx_cr_ledger_idempotency ON content_research_budget_ledger_entries(idempotency_key)")
    conn.execute("CREATE INDEX idx_cr_checkpoint_task_stage ON content_research_stage_checkpoints(subagent_task_id, stage_name)")


_V6_INDEXES = (
    "CREATE UNIQUE INDEX idx_cr_canonical_source_identity ON content_research_canonical_sources(platform, platform_source_kind, platform_source_id)",
    "CREATE UNIQUE INDEX idx_cr_projection_identity ON content_research_direction_source_projections(research_direction_id, canonical_source_id, evidence_packet_id)",
)


def _apply_0006(conn: sqlite3.Connection) -> None:
    for statement in _V6_INDEXES:
        conn.execute(statement)


def _checksum(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _apply_migration(
    conn: sqlite3.Connection,
    *,
    version: str,
    name: str,
    checksum: str,
    apply: Callable[[], None],
) -> None:
    row = conn.execute("SELECT checksum FROM content_research_schema_migrations WHERE version = ?", (version,)).fetchone()
    if row is not None:
        if row[0] != checksum:
            raise RuntimeError(f"content research migration {version} checksum mismatch")
        return
    apply()
    conn.execute(
        "INSERT INTO content_research_schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
        (version, name, checksum, datetime.now(timezone.utc).isoformat()),
    )


def apply_content_research_migrations(
    db_path: str,
    legacy_bootstrap: Callable[[str], None],
    *,
    fail_after_statement: int | None = None,
) -> None:
    migration_0002_sql = _migration_0002_sql()
    legacy_checksum = hashlib.sha256(b"content_research_legacy_bootstrap_v1").hexdigest()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS content_research_schema_migrations (version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
        legacy = conn.execute("SELECT checksum FROM content_research_schema_migrations WHERE version = '0001'").fetchone()
        if legacy is None:
            conn.commit()
            legacy_bootstrap(db_path)
            conn.execute(
                "INSERT INTO content_research_schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                ("0001", "legacy_bootstrap", legacy_checksum, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        elif legacy[0] != legacy_checksum:
            raise RuntimeError("content research migration 0001 checksum mismatch")

        conn.execute("BEGIN IMMEDIATE")
        try:
            def apply_0002() -> None:
                for index, statement in enumerate((item.strip() for item in migration_0002_sql.split(";") if item.strip()), start=1):
                    conn.execute(statement)
                    if fail_after_statement == index:
                        raise RuntimeError("injected migration failure")

            _apply_migration(conn, version="0002", name="formal_research_contracts", checksum=hashlib.sha256(migration_0002_sql.encode("utf-8")).hexdigest(), apply=apply_0002)
            _apply_migration(conn, version="0003", name="formal_entity_columns", checksum=_checksum(_V3_COLUMNS), apply=lambda: _apply_0003(conn))
            _apply_migration(conn, version="0004", name="additional_entity_columns", checksum=_checksum(_V4_COLUMNS), apply=lambda: _apply_0004(conn))
            _apply_migration(conn, version="0005", name="replace_generic_entity_tables", checksum=_checksum(_TYPED_TABLES), apply=lambda: _apply_0005(conn))
            _apply_migration(conn, version="0006", name="role_identity_constraints", checksum=_checksum(_V6_INDEXES), apply=lambda: _apply_0006(conn))
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
