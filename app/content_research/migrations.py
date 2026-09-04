"""Append-only SQLite migrations for the Content Research schema.

Migration bodies are deliberately immutable.  Once a version is published its
SQL/operation definition must never be edited: evolve the schema with a new
version instead.  F003 is not released yet, so 0005 may intentionally discard
the temporary generic-role records created by the early refactor.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone

from app.content_research.execution_decision_identity import (
    LegacyDecisionInput,
    build_execution_decision_identity,
    build_legacy_execution_decision_identity,
)
from app.core.sqlite_connection_roles import open_migration_database

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
    "content_research_canonical_sources": (
        "platform TEXT",
        "platform_source_kind TEXT",
        "platform_source_id TEXT",
        "canonical_url TEXT",
    ),
    "content_research_direction_source_projections": (
        "research_direction_id TEXT",
        "canonical_source_id TEXT",
        "evidence_packet_id TEXT",
        "eligibility_state TEXT",
    ),
    "content_research_directional_evidence_packets": (
        "research_direction_id TEXT",
        "canonical_source_id TEXT",
        "field_projection_hash TEXT",
    ),
    "content_research_claim_candidates": (
        "research_direction_id TEXT",
        "evidence_packet_id TEXT",
        "statement TEXT",
    ),
    "content_research_claim_admission_decisions": (
        "research_direction_id TEXT",
        "claim_candidate_id TEXT",
        "decision TEXT",
        "policy_snapshot_id TEXT",
    ),
    "content_research_stage_checkpoints": (
        "subagent_task_id TEXT",
        "stage_name TEXT",
        "input_fingerprint TEXT",
    ),
    "content_research_budget_ledger_entries": (
        "research_plan_id TEXT",
        "idempotency_key TEXT",
        "reservation_status TEXT",
    ),
}

# Fields discovered after 0003 are a new migration, never a rewrite of 0003.
_V4_COLUMNS = {
    "content_research_direction_result_decisions": (
        "research_direction_id TEXT",
        "policy_snapshot_id TEXT",
    ),
    "content_research_weak_signals": ("admission_decision_id TEXT",),
    "content_research_cross_direction_records": ("research_plan_id TEXT", "record_type TEXT"),
    "content_research_aggregate_claims": ("research_plan_id TEXT", "aggregate_type TEXT"),
    "content_research_stage_checkpoints": ("status TEXT", "retry_count INTEGER NOT NULL DEFAULT 0"),
    "content_research_budget_ledger_entries": (
        "research_direction_id TEXT",
        "reserved_amount REAL",
        "consumed_amount REAL",
        "stage_checkpoint_id TEXT",
    ),
    "content_research_report_faithfulness_decisions": (
        "research_plan_id TEXT",
        "result_snapshot_id TEXT",
    ),
}


def _add_columns(conn: sqlite3.Connection, columns_by_table: dict[str, tuple[str, ...]]) -> None:
    for table, columns in columns_by_table.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column.split()[0] not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")


def _apply_0003(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V3_COLUMNS)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_ledger_idempotency ON content_research_budget_ledger_entries(idempotency_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cr_checkpoint_task_stage ON content_research_stage_checkpoints(subagent_task_id, stage_name)"
    )


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
    conn.execute(
        "CREATE UNIQUE INDEX idx_cr_ledger_idempotency ON content_research_budget_ledger_entries(idempotency_key)"
    )
    conn.execute(
        "CREATE INDEX idx_cr_checkpoint_task_stage ON content_research_stage_checkpoints(subagent_task_id, stage_name)"
    )


_V6_INDEXES = (
    "CREATE UNIQUE INDEX idx_cr_canonical_source_identity ON content_research_canonical_sources(platform, platform_source_kind, platform_source_id)",
    "CREATE UNIQUE INDEX idx_cr_projection_identity ON content_research_direction_source_projections(research_direction_id, canonical_source_id, evidence_packet_id)",
)


def _apply_0006(conn: sqlite3.Connection) -> None:
    for statement in _V6_INDEXES:
        conn.execute(statement)


# 0007 introduces the formal run boundary for packet-derived read models.
_V7_COLUMNS = {
    "content_research_direction_source_projections": ("workflow_run_id TEXT",),
    "content_research_directional_evidence_packets": ("workflow_run_id TEXT",),
    "content_research_stage_checkpoints": ("workflow_run_id TEXT",),
}

_V7_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cr_packet_run_direction ON content_research_directional_evidence_packets(workflow_run_id, research_direction_id)",
    "CREATE INDEX IF NOT EXISTS idx_cr_projection_run_direction ON content_research_direction_source_projections(workflow_run_id, research_direction_id)",
    "CREATE INDEX IF NOT EXISTS idx_cr_checkpoint_run_task ON content_research_stage_checkpoints(workflow_run_id, subagent_task_id, stage_name)",
)


def _apply_0007(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V7_COLUMNS)
    for statement in _V7_INDEXES:
        conn.execute(statement)


_V8_COLUMNS = {
    "content_research_claim_candidates": (
        "workflow_run_id TEXT",
        "intent_id TEXT",
        "claim_type TEXT",
        "requested_state TEXT",
    ),
}


def _apply_0008(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V8_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cr_candidate_run_direction ON content_research_claim_candidates(workflow_run_id, research_direction_id)"
    )


# 0009 replaces the unused generic report-audit role with explicit report
# versions. F003 has not shipped its report path, so no formal report data is
# eligible for migration from the temporary shape.
_V9_REPORT_TABLES = {
    "content_research_report_drafts": "workflow_run_id TEXT NOT NULL, research_plan_id TEXT NOT NULL, governed_snapshot_id TEXT NOT NULL, governed_snapshot_version TEXT NOT NULL, input_fingerprint TEXT NOT NULL, policy_version TEXT NOT NULL, algorithm_version TEXT NOT NULL, previous_version_id TEXT",
    "content_research_report_faithfulness_decisions": "workflow_run_id TEXT NOT NULL, research_plan_id TEXT NOT NULL, governed_snapshot_id TEXT NOT NULL, governed_snapshot_version TEXT NOT NULL, input_fingerprint TEXT NOT NULL, policy_version TEXT NOT NULL, algorithm_version TEXT NOT NULL, report_draft_id TEXT NOT NULL, previous_version_id TEXT",
    "content_research_report_publications": "workflow_run_id TEXT NOT NULL, research_plan_id TEXT NOT NULL, governed_snapshot_id TEXT NOT NULL, governed_snapshot_version TEXT NOT NULL, input_fingerprint TEXT NOT NULL, policy_version TEXT NOT NULL, algorithm_version TEXT NOT NULL, report_draft_id TEXT NOT NULL, faithfulness_decision_id TEXT NOT NULL, publication_state TEXT NOT NULL, previous_version_id TEXT",
}

# 0010 adds explicit stage timing. Existing checkpoints remain readable but do
# not acquire invented timings; only checkpoints with both boundaries expose a
# duration to the public trace.
_V10_COLUMNS = {
    "content_research_stage_checkpoints": ("started_at TEXT", "finished_at TEXT"),
}

# 0011 is the durable hand-off boundary between an HTTP action and a potentially
# slow source provider.  A restart may reclaim an expired lease without relying
# on an in-memory asyncio task.
_V11_DISPATCH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS content_research_dispatch_jobs (
    workflow_run_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    limit_per_specialist INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cr_dispatch_jobs_status_created
    ON content_research_dispatch_jobs(status, created_at);
"""

_V12_DISPATCH_LEASE_COLUMNS = {
    "content_research_dispatch_jobs": (
        "lease_owner TEXT",
        "lease_token TEXT",
        "lease_heartbeat_at TEXT",
    ),
}

# 0013 removes the unreleased aggregate-persistence experiment.  These legacy
# names remain only in this migration so databases created before Gate 4A lose
# the obsolete tables and snapshot column as they advance to the Lite schema.
_V13_LEGACY_TABLES = (
    "content_research_evidence_bundle_items",
    "content_research_evidence_bundles",
)
_V13_LEGACY_SNAPSHOT_COLUMN = "evidence_bundle_ids_json"


def _apply_0013(conn: sqlite3.Connection) -> None:
    for table in _V13_LEGACY_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(content_research_result_snapshots)")
    }
    if _V13_LEGACY_SNAPSHOT_COLUMN in columns:
        conn.execute(
            f"ALTER TABLE content_research_result_snapshots DROP COLUMN {_V13_LEGACY_SNAPSHOT_COLUMN}"
        )


# 0014 cuts Creator and Lite over from the pre-cutover report publication
# lineage. Gate 2 evidence remains the durable audit and replay boundary.
_V14_REPORT_TABLES = (
    "content_research_report_publications",
    "content_research_report_faithfulness_decisions",
    "content_research_report_drafts",
)


def _apply_0014(conn: sqlite3.Connection) -> None:
    publication_rows = list(
        conn.execute(
            "SELECT id, workflow_run_id, report_draft_id, faithfulness_decision_id "
            "FROM content_research_report_publications"
        )
    )
    publication_ids = {row[0] for row in publication_rows}
    workflow_run_ids = sorted({row[1] for row in publication_rows})
    draft_ids = {row[2] for row in publication_rows}
    decision_ids = {row[3] for row in publication_rows}
    if workflow_run_ids:
        workflow_placeholders = ", ".join("?" for _ in workflow_run_ids)
        artifact_rows = list(
            conn.execute(
                f"""
                SELECT artifact_id, payload_json, summary_text, storage_table, storage_key
                FROM workflow_artifacts
                WHERE run_id IN ({workflow_placeholders})
                  AND artifact_type = 'final_result'
                """,
                workflow_run_ids,
            )
        )
        artifact_ids = {row[0] for row in artifact_rows}
        report_artifact_ids: set[str] = set()
        for artifact_id, raw_payload, summary_text, storage_table, storage_key in artifact_rows:
            try:
                payload = json.loads(raw_payload) if raw_payload else None
            except (TypeError, ValueError):
                payload = None
            if (
                summary_text == "内容调研报告已发布"
                or storage_table in _V14_REPORT_TABLES
                or storage_key in publication_ids | draft_ids | decision_ids
                or (
                    isinstance(payload, dict)
                    and (
                        payload.get("schema_version")
                        == "content_research_published_report_artifact_v1"
                        or payload.get("report_publication_id") in publication_ids
                        or payload.get("report_draft_id") in draft_ids
                        or payload.get("faithfulness_decision_id") in decision_ids
                    )
                )
            ):
                report_artifact_ids.add(artifact_id)

        message_rows = list(
            conn.execute(
                f"""
                SELECT id, text, artifact_refs_json
                FROM creator_messages
                WHERE run_id IN ({workflow_placeholders})
                  AND message_type = 'artifact_result'
                """,
                workflow_run_ids,
            )
        )
        parsed_message_refs: dict[str, set[str]] = {}
        report_message_ids: set[str] = set()
        for message_id, message_text, raw_refs in message_rows:
            try:
                refs = json.loads(raw_refs) if raw_refs else []
            except (TypeError, ValueError):
                refs = []
            referenced_artifact_ids = {
                ref.get("artifact_id")
                for ref in refs
                if isinstance(ref, dict)
                and isinstance(ref.get("artifact_id"), str)
                and ref["artifact_id"] in artifact_ids
            }
            parsed_message_refs[message_id] = referenced_artifact_ids
            if message_text == "内容调研报告已生成。":
                report_message_ids.add(message_id)
                report_artifact_ids.update(referenced_artifact_ids)

        report_message_ids.update(
            message_id
            for message_id, referenced_artifact_ids in parsed_message_refs.items()
            if referenced_artifact_ids & report_artifact_ids
        )
        if report_message_ids:
            message_placeholders = ", ".join("?" for _ in report_message_ids)
            conn.execute(
                f"DELETE FROM creator_messages WHERE id IN ({message_placeholders})",
                sorted(report_message_ids),
            )
        if report_artifact_ids:
            artifact_placeholders = ", ".join("?" for _ in report_artifact_ids)
            conn.execute(
                f"DELETE FROM workflow_artifacts WHERE artifact_id IN ({artifact_placeholders})",
                sorted(report_artifact_ids),
            )

    # Report-level tables themselves define the old contract boundary, even
    # when their JSON payloads are malformed. Gate 2 and non-report rows remain.
    for table in _V14_REPORT_TABLES:
        conn.execute(f"DELETE FROM {table}")


_V15_LLM_CONFIGURATION_SQL = """
CREATE TABLE content_research_llm_configurations (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    api_key TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    last_validation_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
)
"""


_V16_SUPERSEDED_LITE_REPORT_TABLES = (
    "content_research_report_publications",
    "content_research_report_faithfulness_decisions",
    "content_research_report_drafts",
)
_V16_MARKETING_CONCLUSION_SQL = """
CREATE TABLE content_research_marketing_conclusion_candidates (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    research_plan_id TEXT NOT NULL,
    track TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE content_research_marketing_conclusion_decisions (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    research_plan_id TEXT NOT NULL,
    candidate_id TEXT,
    track TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_marketing_conclusion_candidate_run_plan_track
    ON content_research_marketing_conclusion_candidates(workflow_run_id, research_plan_id, track);
CREATE INDEX idx_cr_marketing_conclusion_decision_run_plan_track
    ON content_research_marketing_conclusion_decisions(workflow_run_id, research_plan_id, track);
"""


def _apply_0016(conn: sqlite3.Connection) -> None:
    """Discard superseded Lite report artifacts before conclusion records exist."""
    _apply_0014(conn)
    for table in _V16_SUPERSEDED_LITE_REPORT_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.executescript(_V16_MARKETING_CONCLUSION_SQL)


_V17_XHS_CREDENTIAL_SQL = """
CREATE TABLE xhs_local_credentials (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    cookie TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('qr', 'manual_cookie')),
    status TEXT NOT NULL DEFAULT 'authenticated',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _apply_0017(conn: sqlite3.Connection) -> None:
    conn.executescript(_V17_XHS_CREDENTIAL_SQL)


_V18_XHS_CREDENTIAL_STATUS_SQL = """
ALTER TABLE xhs_local_credentials ADD COLUMN failure_code TEXT;
"""


def _apply_0018(conn: sqlite3.Connection) -> None:
    conn.executescript(_V18_XHS_CREDENTIAL_STATUS_SQL)


_V19_SCOPE_CONTRACT_SQL = """
CREATE TABLE content_research_scope_contracts (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    research_plan_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    query_groups_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workflow_run_id, version)
);
CREATE INDEX idx_cr_scope_contract_workflow_version
    ON content_research_scope_contracts(workflow_run_id, version);
CREATE TABLE content_research_scope_audit_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    scope_contract_id TEXT NOT NULL,
    scope_contract_version INTEGER NOT NULL,
    event_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_scope_audit_workflow_version
    ON content_research_scope_audit_events(workflow_run_id, scope_contract_version, created_at);
"""


def _apply_0019(conn: sqlite3.Connection) -> None:
    conn.executescript(_V19_SCOPE_CONTRACT_SQL)


_V20_SCOPE_COVERAGE_SQL = """
CREATE TABLE content_research_scope_coverage_snapshots (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    scope_contract_id TEXT NOT NULL UNIQUE,
    scope_contract_version INTEGER NOT NULL,
    state TEXT NOT NULL,
    constraint_counts_json TEXT NOT NULL,
    unmet_constraint_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_scope_coverage_workflow_version
    ON content_research_scope_coverage_snapshots(workflow_run_id, scope_contract_version);
"""


_V21_SCOPE_DRAFT_SQL = """
CREATE TABLE content_research_scope_drafts (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    research_plan_id TEXT NOT NULL,
    structure_hash TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    query_groups_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_scope_draft_workflow_created
    ON content_research_scope_drafts(workflow_run_id, created_at);
CREATE TABLE content_research_scope_draft_audit_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    scope_draft_id TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


_V22_SCOPE_DRAFT_CONFIRMATION_SQL = """
CREATE TABLE content_research_scope_draft_confirmations (
    scope_draft_id TEXT PRIMARY KEY,
    scope_contract_id TEXT NOT NULL UNIQUE,
    workflow_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


_V23_SCOPE_EXECUTION_AUTHORIZATION_SQL = """
CREATE TABLE content_research_scope_execution_authorizations (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    scope_contract_id TEXT NOT NULL,
    scope_contract_version INTEGER NOT NULL,
    coverage_snapshot_id TEXT NOT NULL UNIQUE,
    resolution TEXT NOT NULL,
    execution_revision INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_scope_execution_authorization_workflow
    ON content_research_scope_execution_authorizations(workflow_run_id, created_at, id);
"""


_V24_SCOPE_EXECUTION_CONTINUATION_SQL = """
CREATE TABLE content_research_scope_coverage_snapshots_v24 (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    scope_contract_id TEXT NOT NULL,
    scope_contract_version INTEGER NOT NULL,
    execution_revision INTEGER NOT NULL,
    execution_authorization_id TEXT UNIQUE,
    source_coverage_snapshot_id TEXT,
    state TEXT NOT NULL,
    constraint_counts_json TEXT NOT NULL,
    unmet_constraint_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(scope_contract_id, execution_revision)
);
INSERT INTO content_research_scope_coverage_snapshots_v24
    (id, workflow_run_id, scope_contract_id, scope_contract_version,
     execution_revision, execution_authorization_id, source_coverage_snapshot_id,
     state, constraint_counts_json, unmet_constraint_ids_json, created_at)
SELECT id, workflow_run_id, scope_contract_id, scope_contract_version,
       1, NULL, NULL, state, constraint_counts_json, unmet_constraint_ids_json, created_at
FROM content_research_scope_coverage_snapshots;
DROP TABLE content_research_scope_coverage_snapshots;
ALTER TABLE content_research_scope_coverage_snapshots_v24
    RENAME TO content_research_scope_coverage_snapshots;
CREATE INDEX idx_cr_scope_coverage_workflow_version
    ON content_research_scope_coverage_snapshots(
        workflow_run_id, scope_contract_version, execution_revision
    );
CREATE TABLE content_research_scope_execution_continuations (
    id TEXT PRIMARY KEY,
    authorization_id TEXT NOT NULL UNIQUE,
    workflow_run_id TEXT NOT NULL,
    execution_revision INTEGER NOT NULL,
    operation TEXT NOT NULL,
    supplementary_queries_json TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX idx_cr_scope_execution_continuation_claim
    ON content_research_scope_execution_continuations(state, created_at, id);
"""


def _apply_0020(conn: sqlite3.Connection) -> None:
    conn.executescript(_V20_SCOPE_COVERAGE_SQL)


def _apply_0021(conn: sqlite3.Connection) -> None:
    conn.executescript(_V21_SCOPE_DRAFT_SQL)


def _apply_0022(conn: sqlite3.Connection) -> None:
    conn.executescript(_V22_SCOPE_DRAFT_CONFIRMATION_SQL)


def _apply_0023(conn: sqlite3.Connection) -> None:
    conn.executescript(_V23_SCOPE_EXECUTION_AUTHORIZATION_SQL)


def _apply_0024(conn: sqlite3.Connection) -> None:
    conn.executescript(_V24_SCOPE_EXECUTION_CONTINUATION_SQL)
    rows = conn.execute(
        """SELECT id, workflow_run_id, scope_contract_id, scope_contract_version,
                  payload_json, created_at
           FROM content_research_scope_audit_events
           WHERE event_name = 'coverage_resolved'
           ORDER BY created_at ASC, id ASC"""
    ).fetchall()
    for (
        event_id,
        workflow_run_id,
        scope_contract_id,
        scope_version,
        raw_payload,
        created_at,
    ) in rows:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            continue
        snapshot_id = str(payload.get("coverage_snapshot_id") or "")
        resolution = str(payload.get("resolution") or "")
        if not snapshot_id or resolution not in {
            "expand_required_constraint",
            "generate_limited_report",
            "relax_constraint",
        }:
            continue
        existing_authorization = conn.execute(
            """SELECT id, execution_revision, resolution, scope_contract_id
               FROM content_research_scope_execution_authorizations
               WHERE coverage_snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
        snapshot_row = conn.execute(
            """SELECT scope_contract_id, execution_revision
               FROM content_research_scope_coverage_snapshots WHERE id=?""",
            (snapshot_id,),
        ).fetchone()
        if snapshot_row is None:
            continue
        execution_revision = (
            max(int(existing_authorization[1]), int(snapshot_row[1]) + 1)
            if existing_authorization is not None
            and str(existing_authorization[3]) == str(snapshot_row[0])
            else (
                int(existing_authorization[1])
                if existing_authorization is not None
                else (
                    int(snapshot_row[1]) + 1
                    if str(snapshot_row[0]) == str(scope_contract_id)
                    else 1
                )
            )
        )
        identity = json.dumps(
            {
                "coverage_snapshot_id": snapshot_id,
                "resolution": resolution,
                "event_id": event_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        authorization_id = (
            str(existing_authorization[0])
            if existing_authorization is not None
            else "sea_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        )
        continuation_id = "sec_" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()[:24]
        state = (
            "authorized_limited_report"
            if resolution == "generate_limited_report"
            else "authorized_collection"
        )
        operation = (
            "limited_report"
            if resolution == "generate_limited_report"
            else "supplementary_collection"
        )
        queries = payload.get("supplementary_queries") or []
        now = created_at or datetime.now(timezone.utc).isoformat()
        if existing_authorization is None:
            conn.execute(
                """INSERT INTO content_research_scope_execution_authorizations
                   (id, workflow_run_id, scope_contract_id, scope_contract_version,
                    coverage_snapshot_id, resolution, execution_revision, state, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    authorization_id,
                    workflow_run_id,
                    scope_contract_id,
                    scope_version,
                    snapshot_id,
                    resolution,
                    execution_revision,
                    state,
                    now,
                ),
            )
        elif int(existing_authorization[1]) != execution_revision:
            conn.execute(
                """UPDATE content_research_scope_execution_authorizations
                   SET execution_revision=? WHERE id=?""",
                (execution_revision, authorization_id),
            )
        conn.execute(
            """INSERT INTO content_research_scope_execution_continuations
               (id, authorization_id, workflow_run_id, execution_revision, operation,
                supplementary_queries_json, state, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                continuation_id,
                authorization_id,
                workflow_run_id,
                execution_revision,
                operation,
                json.dumps(queries, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )


_V25_EXECUTION_UNITS_SQL = """
CREATE TABLE content_research_scope_execution_units (
    id TEXT PRIMARY KEY,
    decision_fingerprint TEXT NOT NULL UNIQUE,
    workflow_run_id TEXT NOT NULL,
    scope_contract_id TEXT NOT NULL,
    coverage_snapshot_id TEXT NOT NULL UNIQUE,
    resolution TEXT NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_execution_unit_workflow_scope
    ON content_research_scope_execution_units(workflow_run_id, scope_contract_id);
CREATE TABLE content_research_scope_execution_attempts (
    execution_unit_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    state TEXT NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    provider_state TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(execution_unit_id, attempt_no),
    FOREIGN KEY(execution_unit_id) REFERENCES content_research_scope_execution_units(id)
);
CREATE INDEX idx_cr_execution_attempt_unit_attempt
    ON content_research_scope_execution_attempts(execution_unit_id, attempt_no);
CREATE TABLE content_research_execution_facts (
    execution_unit_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(execution_unit_id, attempt_no, sequence_no),
    FOREIGN KEY(execution_unit_id, attempt_no)
        REFERENCES content_research_scope_execution_attempts(execution_unit_id, attempt_no)
);
CREATE INDEX idx_cr_execution_fact_unit_attempt_sequence
    ON content_research_execution_facts(execution_unit_id, attempt_no, sequence_no);
ALTER TABLE content_research_scope_execution_authorizations
    ADD COLUMN execution_unit_id TEXT;
ALTER TABLE content_research_scope_execution_continuations
    ADD COLUMN execution_unit_id TEXT;
"""


def _ensure_execution_identity_columns(conn: sqlite3.Connection) -> None:
    """Make both fresh 0025 and pre-identity 0025 tables safe for reconciliation."""
    _add_columns(
        conn,
        {
            "content_research_scope_execution_units": (
                "identity_schema TEXT NOT NULL DEFAULT 'execution_decision_identity_v1'",
                "identity_json TEXT NOT NULL DEFAULT '{}'",
                "identity_state TEXT NOT NULL DEFAULT 'legacy_identity_incomplete'",
                "legacy_authorization_id TEXT",
            )
        },
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_execution_unit_canonical_fingerprint
           ON content_research_scope_execution_units(decision_fingerprint)
           WHERE identity_state='canonical'"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_execution_unit_legacy_authorization
           ON content_research_scope_execution_units(legacy_authorization_id)
           WHERE legacy_authorization_id IS NOT NULL"""
    )


def _remove_legacy_global_fingerprint_uniqueness(conn: sqlite3.Connection) -> None:
    """Rebuild the execution tables so only canonical fingerprints are unique."""
    table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='content_research_scope_execution_units'"
    ).fetchone()
    if table_sql_row is None or "decision_fingerprint TEXT NOT NULL UNIQUE" not in str(
        table_sql_row[0]
    ):
        return

    units = conn.execute(
        """SELECT id, decision_fingerprint, workflow_run_id, scope_contract_id,
                  coverage_snapshot_id, resolution, operation, state, created_at,
                  identity_schema, identity_json, identity_state, legacy_authorization_id
           FROM content_research_scope_execution_units"""
    ).fetchall()
    attempts = conn.execute(
        """SELECT execution_unit_id, attempt_no, state, lease_owner, lease_token,
                  lease_expires_at, provider_state, created_at
           FROM content_research_scope_execution_attempts"""
    ).fetchall()
    facts = conn.execute(
        """SELECT execution_unit_id, attempt_no, sequence_no, kind, payload_json, created_at
           FROM content_research_execution_facts"""
    ).fetchall()

    conn.execute("DROP TABLE content_research_execution_facts")
    conn.execute("DROP TABLE content_research_scope_execution_attempts")
    conn.execute("DROP TABLE content_research_scope_execution_units")
    conn.execute(
        """CREATE TABLE content_research_scope_execution_units (
               id TEXT PRIMARY KEY,
               decision_fingerprint TEXT NOT NULL,
               workflow_run_id TEXT NOT NULL,
               scope_contract_id TEXT NOT NULL,
               coverage_snapshot_id TEXT NOT NULL UNIQUE,
               resolution TEXT NOT NULL,
               operation TEXT NOT NULL,
               state TEXT NOT NULL,
               created_at TEXT NOT NULL,
               identity_schema TEXT NOT NULL,
               identity_json TEXT NOT NULL,
               identity_state TEXT NOT NULL,
               legacy_authorization_id TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX idx_cr_execution_unit_workflow_scope
           ON content_research_scope_execution_units(workflow_run_id, scope_contract_id)"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX idx_cr_execution_unit_canonical_fingerprint
           ON content_research_scope_execution_units(decision_fingerprint)
           WHERE identity_state='canonical'"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX idx_cr_execution_unit_legacy_authorization
           ON content_research_scope_execution_units(legacy_authorization_id)
           WHERE legacy_authorization_id IS NOT NULL"""
    )
    conn.executemany(
        "INSERT INTO content_research_scope_execution_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        units,
    )

    conn.execute(
        """CREATE TABLE content_research_scope_execution_attempts (
               execution_unit_id TEXT NOT NULL,
               attempt_no INTEGER NOT NULL,
               state TEXT NOT NULL,
               lease_owner TEXT,
               lease_token TEXT,
               lease_expires_at TEXT,
               provider_state TEXT,
               created_at TEXT NOT NULL,
               PRIMARY KEY(execution_unit_id, attempt_no),
               FOREIGN KEY(execution_unit_id) REFERENCES content_research_scope_execution_units(id)
           )"""
    )
    conn.execute(
        """CREATE INDEX idx_cr_execution_attempt_unit_attempt
           ON content_research_scope_execution_attempts(execution_unit_id, attempt_no)"""
    )
    conn.executemany(
        "INSERT INTO content_research_scope_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        attempts,
    )

    conn.execute(
        """CREATE TABLE content_research_execution_facts (
               execution_unit_id TEXT NOT NULL,
               attempt_no INTEGER NOT NULL,
               sequence_no INTEGER NOT NULL,
               kind TEXT NOT NULL,
               payload_json TEXT NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY(execution_unit_id, attempt_no, sequence_no),
               FOREIGN KEY(execution_unit_id, attempt_no)
                   REFERENCES content_research_scope_execution_attempts(execution_unit_id, attempt_no)
           )"""
    )
    conn.execute(
        """CREATE INDEX idx_cr_execution_fact_unit_attempt_sequence
           ON content_research_execution_facts(execution_unit_id, attempt_no, sequence_no)"""
    )
    conn.executemany(
        "INSERT INTO content_research_execution_facts VALUES (?, ?, ?, ?, ?, ?)",
        facts,
    )


def _legacy_decision_from_row(row: sqlite3.Row | tuple[object, ...]):
    (
        authorization_id,
        _workflow_run_id,
        resulting_scope_contract_id,
        coverage_snapshot_id,
        resolution,
        _created_at,
        operation,
        _continuation_state,
        _lease_owner,
        _lease_token,
        _lease_expires_at,
        supplementary_queries_json,
        source_scope_contract_id,
        target_constraint_id,
    ) = row
    operation = operation or (
        "limited_report" if resolution == "generate_limited_report" else "supplementary_collection"
    )
    queries = tuple(json.loads(supplementary_queries_json or "[]"))
    return build_legacy_execution_decision_identity(
        LegacyDecisionInput(
            legacy_authorization_id=str(authorization_id),
            coverage_snapshot_id=str(coverage_snapshot_id),
            source_scope_contract_id=str(source_scope_contract_id),
            resulting_scope_contract_id=str(resulting_scope_contract_id),
            resolution=str(resolution),
            operation=str(operation),
            target_constraint_id=(str(target_constraint_id) if target_constraint_id else None),
            supplementary_queries=queries,
        )
    )


def _apply_0025(conn: sqlite3.Connection) -> None:
    """Backfill legacy authorization rows as aliases without changing Scope meaning."""
    # Do not use sqlite3.executescript here: it commits any active transaction
    # before executing, which would strand schema changes if backfill fails.
    for statement in _V25_EXECUTION_UNITS_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)
    _ensure_execution_identity_columns(conn)
    rows = conn.execute(
        """SELECT authorization.id, authorization.workflow_run_id, authorization.scope_contract_id,
                  authorization.coverage_snapshot_id, authorization.resolution,
                  authorization.created_at, continuation.operation, continuation.state,
                  continuation.lease_owner, continuation.lease_token,
                  continuation.lease_expires_at, continuation.supplementary_queries_json,
                  snapshot.scope_contract_id,
                  (SELECT json_extract(event.payload_json, '$.constraint_id')
                     FROM content_research_scope_audit_events AS event
                    WHERE event.event_name='coverage_resolved'
                      AND json_extract(event.payload_json, '$.coverage_snapshot_id')=authorization.coverage_snapshot_id
                    ORDER BY event.created_at ASC, event.id ASC
                    LIMIT 1)
           FROM content_research_scope_execution_authorizations AS authorization
           LEFT JOIN content_research_scope_execution_continuations AS continuation
             ON continuation.authorization_id=authorization.id
           LEFT JOIN content_research_scope_coverage_snapshots AS snapshot
             ON snapshot.id=authorization.coverage_snapshot_id
           ORDER BY authorization.created_at ASC, authorization.id ASC"""
    ).fetchall()
    for row in rows:
        (
            authorization_id,
            workflow_run_id,
            scope_contract_id,
            coverage_snapshot_id,
            resolution,
            created_at,
            operation,
            continuation_state,
            lease_owner,
            lease_token,
            lease_expires_at,
            supplementary_queries_json,
            source_scope_contract_id,
            target_constraint_id,
        ) = row
        legacy_identity = _legacy_decision_from_row(row)
        unit_id = legacy_identity.execution_unit_id
        state = continuation_state or "pending"
        now = created_at or datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO content_research_scope_execution_units
               (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                coverage_snapshot_id, resolution, operation, state, created_at,
                identity_schema, identity_json, identity_state, legacy_authorization_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                unit_id,
                legacy_identity.decision_fingerprint,
                workflow_run_id,
                scope_contract_id,
                coverage_snapshot_id,
                resolution,
                "limited_report"
                if resolution == "generate_limited_report"
                else "supplementary_collection",
                state if state in {"pending", "running", "completed", "failed"} else "failed",
                now,
                legacy_identity.identity_schema,
                legacy_identity.identity_json,
                legacy_identity.identity_state,
                authorization_id,
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO content_research_scope_execution_attempts
               (execution_unit_id, attempt_no, state, lease_owner, lease_token,
                lease_expires_at, provider_state, created_at)
               VALUES (?, 0, ?, ?, ?, ?, NULL, ?)""",
            (
                unit_id,
                state if state in {"pending", "running", "completed", "failed"} else "failed",
                lease_owner,
                lease_token,
                lease_expires_at,
                now,
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO content_research_execution_facts
               (execution_unit_id, attempt_no, sequence_no, kind, payload_json, created_at)
               VALUES (?, 0, 1, 'decision_accepted', ?, ?)""",
            (
                unit_id,
                json.dumps(
                    {"authorization_id": authorization_id, "migration": "legacy_alias_v1"},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
            ),
        )
        conn.execute(
            """UPDATE content_research_scope_execution_authorizations
               SET execution_unit_id=? WHERE id=?""",
            (unit_id, authorization_id),
        )
        conn.execute(
            """UPDATE content_research_scope_execution_continuations
               SET execution_unit_id=? WHERE authorization_id=?""",
            (unit_id, authorization_id),
        )


def _apply_0026(conn: sqlite3.Connection) -> None:
    """Repair pre-canonical 0025 aliases without changing their stable unit IDs."""
    _ensure_execution_identity_columns(conn)
    rows = conn.execute(
        """SELECT unit.id, authorization.id, authorization.workflow_run_id,
                  authorization.scope_contract_id, authorization.coverage_snapshot_id,
                  authorization.resolution, authorization.created_at,
                  continuation.operation, continuation.state, continuation.lease_owner,
                  continuation.lease_token, continuation.lease_expires_at,
                  continuation.supplementary_queries_json, snapshot.scope_contract_id,
                  (SELECT json_extract(event.payload_json, '$.constraint_id')
                     FROM content_research_scope_audit_events AS event
                    WHERE event.event_name='coverage_resolved'
                      AND json_extract(event.payload_json, '$.coverage_snapshot_id')=authorization.coverage_snapshot_id
                    ORDER BY event.created_at ASC, event.id ASC
                    LIMIT 1)
           FROM content_research_scope_execution_units AS unit
           JOIN content_research_scope_execution_authorizations AS authorization
             ON authorization.execution_unit_id=unit.id
           JOIN content_research_scope_coverage_snapshots AS snapshot
             ON snapshot.id=authorization.coverage_snapshot_id
           LEFT JOIN content_research_scope_execution_continuations AS continuation
             ON continuation.authorization_id=authorization.id"""
    ).fetchall()
    for row in rows:
        unit_id = str(row[0])
        legacy_identity = _legacy_decision_from_row(row[1:])
        conn.execute(
            """UPDATE content_research_scope_execution_units
               SET decision_fingerprint=?, identity_schema=?, identity_json=?,
                   identity_state=?, legacy_authorization_id=?
               WHERE id=?""",
            (
                legacy_identity.decision_fingerprint,
                legacy_identity.identity_schema,
                legacy_identity.identity_json,
                legacy_identity.identity_state,
                row[1],
                unit_id,
            ),
        )
    _remove_legacy_global_fingerprint_uniqueness(conn)


def _apply_0028(conn: sqlite3.Connection) -> None:
    """Rewrite pre-minimal canonical identities and their stable references."""
    units = conn.execute(
        """SELECT id, decision_fingerprint, workflow_run_id, scope_contract_id,
                  coverage_snapshot_id, resolution, operation, state, created_at,
                  identity_schema, identity_json, identity_state, legacy_authorization_id
           FROM content_research_scope_execution_units"""
    ).fetchall()
    attempts = conn.execute(
        """SELECT execution_unit_id, attempt_no, state, lease_owner, lease_token,
                  lease_expires_at, provider_state, created_at
           FROM content_research_scope_execution_attempts"""
    ).fetchall()
    facts = conn.execute(
        """SELECT execution_unit_id, attempt_no, sequence_no, kind, payload_json, created_at
           FROM content_research_execution_facts"""
    ).fetchall()

    id_map: dict[str, str] = {}
    decision_payloads: dict[str, dict[str, object]] = {}
    repaired_units: list[tuple[object, ...]] = []
    for row in units:
        old_id = str(row[0])
        try:
            persisted_identity = json.loads(str(row[10]))
        except json.JSONDecodeError as exc:
            raise RuntimeError("execution unit identity JSON is invalid") from exc
        if not isinstance(persisted_identity, dict):
            raise RuntimeError("execution unit identity JSON must be an object")
        if row[11] == "canonical":
            rebuilt = build_execution_decision_identity(
                coverage_snapshot_id=str(persisted_identity["coverage_snapshot_id"]),
                source_scope_contract_id=str(persisted_identity["source_scope_contract_id"]),
                resulting_scope_contract_id=str(persisted_identity["resulting_scope_contract_id"]),
                resolution=str(persisted_identity["resolution"]),
                target_constraint_id=(
                    str(persisted_identity["target_constraint_id"])
                    if persisted_identity.get("target_constraint_id") is not None
                    else None
                ),
                supplementary_queries=tuple(
                    str(value) for value in persisted_identity.get("supplementary_queries", ())
                ),
            )
            new_id = rebuilt.execution_unit_id
            fingerprint = rebuilt.decision_fingerprint
            identity_json = rebuilt.canonical_json
            decision_payloads[old_id] = rebuilt.payload
        else:
            persisted_identity.pop("operation", None)
            new_id = old_id
            fingerprint = str(row[1])
            identity_json = json.dumps(
                persisted_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        id_map[old_id] = new_id
        repaired_units.append(
            (
                new_id,
                fingerprint,
                *row[2:10],
                identity_json,
                row[11],
                row[12],
            )
        )

    if len(set(id_map.values())) != len(id_map):
        raise RuntimeError("execution unit identity repair produced duplicate unit IDs")

    conn.execute("DROP TABLE content_research_execution_facts")
    conn.execute("DROP TABLE content_research_scope_execution_attempts")
    conn.execute("DROP TABLE content_research_scope_execution_units")
    conn.execute(
        """CREATE TABLE content_research_scope_execution_units (
               id TEXT PRIMARY KEY,
               decision_fingerprint TEXT NOT NULL,
               workflow_run_id TEXT NOT NULL,
               scope_contract_id TEXT NOT NULL,
               coverage_snapshot_id TEXT NOT NULL UNIQUE,
               resolution TEXT NOT NULL,
               operation TEXT NOT NULL,
               state TEXT NOT NULL,
               created_at TEXT NOT NULL,
               identity_schema TEXT NOT NULL,
               identity_json TEXT NOT NULL,
               identity_state TEXT NOT NULL,
               legacy_authorization_id TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX idx_cr_execution_unit_workflow_scope
           ON content_research_scope_execution_units(workflow_run_id, scope_contract_id)"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX idx_cr_execution_unit_canonical_fingerprint
           ON content_research_scope_execution_units(decision_fingerprint)
           WHERE identity_state='canonical'"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX idx_cr_execution_unit_legacy_authorization
           ON content_research_scope_execution_units(legacy_authorization_id)
           WHERE legacy_authorization_id IS NOT NULL"""
    )
    conn.executemany(
        "INSERT INTO content_research_scope_execution_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        repaired_units,
    )

    conn.execute(
        """CREATE TABLE content_research_scope_execution_attempts (
               execution_unit_id TEXT NOT NULL,
               attempt_no INTEGER NOT NULL,
               state TEXT NOT NULL,
               lease_owner TEXT,
               lease_token TEXT,
               lease_expires_at TEXT,
               provider_state TEXT,
               created_at TEXT NOT NULL,
               PRIMARY KEY(execution_unit_id, attempt_no),
               FOREIGN KEY(execution_unit_id) REFERENCES content_research_scope_execution_units(id)
           )"""
    )
    conn.execute(
        """CREATE INDEX idx_cr_execution_attempt_unit_attempt
           ON content_research_scope_execution_attempts(execution_unit_id, attempt_no)"""
    )
    conn.executemany(
        "INSERT INTO content_research_scope_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(id_map[str(row[0])], *row[1:]) for row in attempts],
    )

    conn.execute(
        """CREATE TABLE content_research_execution_facts (
               execution_unit_id TEXT NOT NULL,
               attempt_no INTEGER NOT NULL,
               sequence_no INTEGER NOT NULL,
               kind TEXT NOT NULL,
               payload_json TEXT NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY(execution_unit_id, attempt_no, sequence_no),
               FOREIGN KEY(execution_unit_id, attempt_no)
                   REFERENCES content_research_scope_execution_attempts(execution_unit_id, attempt_no)
           )"""
    )
    conn.execute(
        """CREATE INDEX idx_cr_execution_fact_unit_attempt_sequence
           ON content_research_execution_facts(execution_unit_id, attempt_no, sequence_no)"""
    )
    repaired_facts: list[tuple[object, ...]] = []
    for row in facts:
        old_id = str(row[0])
        payload_json = str(row[4])
        if row[3] == "decision_accepted" and old_id in decision_payloads:
            payload = json.loads(payload_json)
            if isinstance(payload, dict) and isinstance(payload.get("decision"), dict):
                payload["decision"] = decision_payloads[old_id]
                payload_json = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
        repaired_facts.append((id_map[old_id], *row[1:4], payload_json, row[5]))
    conn.executemany(
        "INSERT INTO content_research_execution_facts VALUES (?, ?, ?, ?, ?, ?)",
        repaired_facts,
    )
    for old_id, new_id in id_map.items():
        if old_id == new_id:
            continue
        conn.execute(
            """UPDATE content_research_scope_execution_authorizations
               SET execution_unit_id=? WHERE execution_unit_id=?""",
            (new_id, old_id),
        )
        conn.execute(
            """UPDATE content_research_scope_execution_continuations
               SET execution_unit_id=? WHERE execution_unit_id=?""",
            (new_id, old_id),
        )


_V29_EXECUTION_LINEAGE_COLUMNS = {
    "content_research_directional_evidence_packets": (
        "scope_contract_id TEXT",
        "execution_unit_id TEXT",
        "attempt_no INTEGER NOT NULL DEFAULT 0",
        "execution_revision INTEGER NOT NULL DEFAULT 1",
    ),
    "content_research_claim_candidates": (
        "scope_contract_id TEXT",
        "execution_unit_id TEXT",
        "attempt_no INTEGER NOT NULL DEFAULT 0",
        "execution_revision INTEGER NOT NULL DEFAULT 1",
    ),
    "content_research_stage_checkpoints": (
        "scope_contract_id TEXT",
        "execution_unit_id TEXT",
        "attempt_no INTEGER NOT NULL DEFAULT 0",
        "execution_revision INTEGER NOT NULL DEFAULT 1",
    ),
    "content_research_scope_coverage_snapshots": (
        "execution_unit_id TEXT",
        "attempt_no INTEGER NOT NULL DEFAULT 0",
        "evidence_manifest_json TEXT",
    ),
}

_V29_EXECUTION_LINEAGE_INDEXES = (
    "CREATE INDEX idx_cr_packet_execution_lineage ON content_research_directional_evidence_packets(workflow_run_id, scope_contract_id, execution_unit_id, attempt_no, execution_revision)",
    "CREATE INDEX idx_cr_candidate_execution_lineage ON content_research_claim_candidates(workflow_run_id, scope_contract_id, execution_unit_id, attempt_no, execution_revision)",
    "CREATE INDEX idx_cr_checkpoint_execution_lineage ON content_research_stage_checkpoints(workflow_run_id, scope_contract_id, execution_unit_id, attempt_no, execution_revision)",
)

_V30_REPORT_LINEAGE_COLUMNS = {
    "content_research_report_drafts": (
        "scope_contract_id TEXT",
        "execution_unit_id TEXT",
        "coverage_snapshot_id TEXT",
        "attempt_no INTEGER",
    ),
    "content_research_report_faithfulness_decisions": (
        "scope_contract_id TEXT",
        "execution_unit_id TEXT",
        "coverage_snapshot_id TEXT",
        "attempt_no INTEGER",
    ),
    "content_research_report_publications": (
        "scope_contract_id TEXT",
        "execution_unit_id TEXT",
        "coverage_snapshot_id TEXT",
        "attempt_no INTEGER",
    ),
}

_V30_REPORT_LINEAGE_INDEXES = (
    "CREATE INDEX idx_cr_report_draft_execution_lineage ON content_research_report_drafts(workflow_run_id, scope_contract_id, execution_unit_id, attempt_no)",
    "CREATE INDEX idx_cr_report_publication_execution_lineage ON content_research_report_publications(workflow_run_id, scope_contract_id, execution_unit_id, attempt_no)",
)

_V31_REPORT_INTEGRITY_EVENT_STATEMENTS = (
    """CREATE TABLE content_research_report_integrity_events (
           id TEXT PRIMARY KEY,
           publication_id TEXT NOT NULL,
           workflow_run_id TEXT NOT NULL,
           event_type TEXT NOT NULL,
           reason_code TEXT NOT NULL,
           recovery_guidance TEXT NOT NULL,
           created_at TEXT NOT NULL,
           FOREIGN KEY(publication_id)
               REFERENCES content_research_report_publications(id)
       )""",
    """CREATE INDEX idx_cr_report_integrity_publication_created
       ON content_research_report_integrity_events(publication_id, created_at, id)""",
    """CREATE INDEX idx_cr_report_integrity_attempt
       ON content_research_report_integrity_events(workflow_run_id, event_type)""",
)


def _apply_0029(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V29_EXECUTION_LINEAGE_COLUMNS)
    for statement in _V29_EXECUTION_LINEAGE_INDEXES:
        conn.execute(statement)


def _apply_0030(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V30_REPORT_LINEAGE_COLUMNS)
    for statement in _V30_REPORT_LINEAGE_INDEXES:
        conn.execute(statement)


def _apply_0031(conn: sqlite3.Connection) -> None:
    for statement in _V31_REPORT_INTEGRITY_EVENT_STATEMENTS:
        conn.execute(statement)


_V32_SCOPE_DRAFT_VERSION_COLUMNS = {
    "content_research_scope_drafts": (
        "schema_version TEXT NOT NULL DEFAULT 'content_research_scope_contract_v1'",
        "core_object TEXT",
        "product_experience_aspect TEXT",
        "context_audience_aspect TEXT",
    ),
}


def _apply_0032(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V32_SCOPE_DRAFT_VERSION_COLUMNS)


_V33_LIFECYCLE_AUTHORITY_SQL = """
CREATE TABLE content_research_state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    event TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    reason_code TEXT,
    error_json TEXT,
    attempt_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, state_revision)
);
CREATE INDEX idx_cr_state_transition_run_revision
    ON content_research_state_transitions(run_id, state_revision);
CREATE TABLE content_research_lifecycle_commands (
    command_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    command_kind TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    result_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cr_lifecycle_command_run_created
    ON content_research_lifecycle_commands(run_id, created_at, command_id);
"""

_V33_WORKFLOW_RUN_COLUMNS = {
    "workflow_runs": (
        "content_research_state TEXT",
        "state_revision INTEGER",
        "state_entered_at TEXT",
        "lifecycle_error_json TEXT",
        "lifecycle_schema_version TEXT",
    ),
}


def _apply_0033(conn: sqlite3.Connection) -> None:
    conn.executescript(_V33_LIFECYCLE_AUTHORITY_SQL)
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
    ).fetchone():
        _add_columns(conn, _V33_WORKFLOW_RUN_COLUMNS)


_V34_MARKETING_ANALYSIS_IDENTITY_SQL = """
CREATE TABLE content_research_evidence_snapshots (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    scope_contract_id TEXT NOT NULL,
    retrieval_execution_unit_id TEXT NOT NULL,
    retrieval_attempt_no INTEGER NOT NULL CHECK(retrieval_attempt_no >= 1),
    snapshot_fingerprint TEXT NOT NULL,
    query_groups_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(retrieval_execution_unit_id, retrieval_attempt_no)
);
CREATE INDEX idx_cr_evidence_snapshot_run_created
    ON content_research_evidence_snapshots(workflow_run_id, created_at, id);
CREATE TABLE content_research_evidence_snapshot_notes (
    snapshot_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    title_hash TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    query_provenance_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, note_id),
    FOREIGN KEY(snapshot_id) REFERENCES content_research_evidence_snapshots(id)
);
CREATE INDEX idx_cr_evidence_snapshot_note_account
    ON content_research_evidence_snapshot_notes(snapshot_id, account_id, note_id);
CREATE TABLE content_research_analysis_units (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    evidence_snapshot_id TEXT NOT NULL,
    contract_fingerprint TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response_schema_hash TEXT NOT NULL,
    embedding_fingerprint_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    verifier_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(evidence_snapshot_id, contract_fingerprint),
    FOREIGN KEY(evidence_snapshot_id) REFERENCES content_research_evidence_snapshots(id)
);
CREATE INDEX idx_cr_analysis_unit_run_created
    ON content_research_analysis_units(workflow_run_id, created_at, id);
CREATE TABLE content_research_analysis_attempts (
    id TEXT PRIMARY KEY,
    analysis_unit_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    successor_of_attempt_id TEXT,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    terminal_at TEXT,
    UNIQUE(analysis_unit_id, attempt_no),
    FOREIGN KEY(analysis_unit_id) REFERENCES content_research_analysis_units(id),
    FOREIGN KEY(successor_of_attempt_id) REFERENCES content_research_analysis_attempts(id)
);
CREATE UNIQUE INDEX idx_cr_analysis_attempt_one_active
    ON content_research_analysis_attempts(analysis_unit_id)
    WHERE state IN ('queued', 'running');
CREATE UNIQUE INDEX idx_cr_analysis_attempt_one_successor
    ON content_research_analysis_attempts(successor_of_attempt_id)
    WHERE successor_of_attempt_id IS NOT NULL;
CREATE TABLE content_research_analysis_checkpoints (
    id TEXT PRIMARY KEY,
    analysis_unit_id TEXT NOT NULL,
    track TEXT NOT NULL CHECK(track IN ('shared', 'need', 'value', 'message')),
    stage TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    output_refs_json TEXT NOT NULL DEFAULT '[]',
    result_checksum TEXT,
    completed_by_attempt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(analysis_unit_id, track, stage, input_fingerprint),
    FOREIGN KEY(analysis_unit_id) REFERENCES content_research_analysis_units(id),
    FOREIGN KEY(completed_by_attempt_id) REFERENCES content_research_analysis_attempts(id)
);
CREATE INDEX idx_cr_analysis_checkpoint_unit_track_stage
    ON content_research_analysis_checkpoints(analysis_unit_id, track, stage, status);
"""


def _apply_0034(conn: sqlite3.Connection) -> None:
    for statement in (
        item.strip() for item in _V34_MARKETING_ANALYSIS_IDENTITY_SQL.split(";") if item.strip()
    ):
        conn.execute(statement)


_V35_MARKETING_ANALYSIS_JOB_SQL = """
CREATE TABLE content_research_analysis_jobs (
    analysis_unit_id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    research_plan_id TEXT NOT NULL,
    coverage_snapshot_id TEXT NOT NULL,
    execution_authorization_id TEXT,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(analysis_unit_id) REFERENCES content_research_analysis_units(id)
);
CREATE INDEX idx_cr_analysis_job_run_created
    ON content_research_analysis_jobs(workflow_run_id, created_at, analysis_unit_id);
"""


def _apply_0035(conn: sqlite3.Connection) -> None:
    conn.executescript(_V35_MARKETING_ANALYSIS_JOB_SQL)


_V36_TRACE_REVISION_SQL = """
CREATE TABLE content_research_trace_revisions (
    workflow_run_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at TEXT NOT NULL
);
CREATE TRIGGER cr_trace_revision_transition_insert
AFTER INSERT ON content_research_state_transitions BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_trace_insert
AFTER INSERT ON content_research_traces BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_observation_insert
AFTER INSERT ON content_research_observation_events BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_execution_unit_insert
AFTER INSERT ON content_research_scope_execution_units BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_execution_unit_update
AFTER UPDATE ON content_research_scope_execution_units BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_execution_fact_insert
AFTER INSERT ON content_research_execution_facts BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    SELECT workflow_run_id, 1, CURRENT_TIMESTAMP
    FROM content_research_scope_execution_units WHERE id=NEW.execution_unit_id
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_stage_checkpoint_insert
AFTER INSERT ON content_research_stage_checkpoints BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_stage_checkpoint_update
AFTER UPDATE ON content_research_stage_checkpoints BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_analysis_attempt_insert
AFTER INSERT ON content_research_analysis_attempts BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    SELECT workflow_run_id, 1, CURRENT_TIMESTAMP
    FROM content_research_analysis_units WHERE id=NEW.analysis_unit_id
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_analysis_attempt_update
AFTER UPDATE ON content_research_analysis_attempts BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    SELECT workflow_run_id, 1, CURRENT_TIMESTAMP
    FROM content_research_analysis_units WHERE id=NEW.analysis_unit_id
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_analysis_checkpoint_insert
AFTER INSERT ON content_research_analysis_checkpoints BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    SELECT workflow_run_id, 1, CURRENT_TIMESTAMP
    FROM content_research_analysis_units WHERE id=NEW.analysis_unit_id
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_publication_insert
AFTER INSERT ON content_research_report_publications BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
CREATE TRIGGER cr_trace_revision_integrity_event_insert
AFTER INSERT ON content_research_report_integrity_events BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    VALUES (NEW.workflow_run_id, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
"""


def _apply_0036(conn: sqlite3.Connection) -> None:
    conn.executescript(_V36_TRACE_REVISION_SQL)


_V37_ANALYSIS_AUTHORITY_SQL = """
ALTER TABLE content_research_analysis_checkpoints
    ADD COLUMN private_result_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE workflow_runs
    ADD COLUMN effective_analysis_attempt_id TEXT;
UPDATE workflow_runs
SET effective_analysis_attempt_id=(
    SELECT attempt.id
    FROM content_research_analysis_attempts AS attempt
    JOIN content_research_analysis_units AS unit
      ON unit.id=attempt.analysis_unit_id
    WHERE unit.workflow_run_id=workflow_runs.run_id
    ORDER BY attempt.attempt_no DESC, attempt.created_at DESC
    LIMIT 1
)
WHERE effective_analysis_attempt_id IS NULL
  AND EXISTS (
    SELECT 1
    FROM content_research_analysis_attempts AS attempt
    JOIN content_research_analysis_units AS unit
      ON unit.id=attempt.analysis_unit_id
    WHERE unit.workflow_run_id=workflow_runs.run_id
  );
CREATE INDEX idx_workflow_run_effective_analysis_attempt
    ON workflow_runs(effective_analysis_attempt_id);
"""


def _apply_0037(conn: sqlite3.Connection) -> None:
    conn.execute(
        "ALTER TABLE content_research_analysis_checkpoints "
        "ADD COLUMN private_result_json TEXT NOT NULL DEFAULT '{}'"
    )
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
        ).fetchone()
        is None
    ):
        return
    workflow_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(workflow_runs)")}
    if "effective_analysis_attempt_id" not in workflow_columns:
        conn.execute("ALTER TABLE workflow_runs ADD COLUMN effective_analysis_attempt_id TEXT")
    conn.execute(
        "UPDATE workflow_runs SET effective_analysis_attempt_id=("
        "SELECT attempt.id FROM content_research_analysis_attempts AS attempt "
        "JOIN content_research_analysis_units AS unit "
        "ON unit.id=attempt.analysis_unit_id "
        "WHERE unit.workflow_run_id=workflow_runs.run_id "
        "ORDER BY attempt.attempt_no DESC, attempt.created_at DESC LIMIT 1) "
        "WHERE effective_analysis_attempt_id IS NULL AND EXISTS ("
        "SELECT 1 FROM content_research_analysis_attempts AS attempt "
        "JOIN content_research_analysis_units AS unit "
        "ON unit.id=attempt.analysis_unit_id "
        "WHERE unit.workflow_run_id=workflow_runs.run_id)"
    )
    conn.execute(
        "CREATE INDEX idx_workflow_run_effective_analysis_attempt "
        "ON workflow_runs(effective_analysis_attempt_id)"
    )


_V38_ATTEMPT_SCOPED_ANALYSIS_FAILURES_SQL = """
DROP TRIGGER IF EXISTS cr_trace_revision_analysis_checkpoint_insert;
ALTER TABLE content_research_analysis_checkpoints
    RENAME TO content_research_analysis_checkpoints_v37;
DROP INDEX idx_cr_analysis_checkpoint_unit_track_stage;
CREATE TABLE content_research_analysis_checkpoints (
    id TEXT PRIMARY KEY,
    analysis_unit_id TEXT NOT NULL,
    track TEXT NOT NULL CHECK(track IN ('shared', 'need', 'value', 'message')),
    stage TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    output_refs_json TEXT NOT NULL DEFAULT '[]',
    result_checksum TEXT,
    private_result_json TEXT NOT NULL DEFAULT '{}',
    completed_by_attempt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(analysis_unit_id) REFERENCES content_research_analysis_units(id),
    FOREIGN KEY(completed_by_attempt_id) REFERENCES content_research_analysis_attempts(id)
);
INSERT INTO content_research_analysis_checkpoints (
    id, analysis_unit_id, track, stage, input_fingerprint, status,
    output_refs_json, result_checksum, private_result_json,
    completed_by_attempt_id, created_at, updated_at
)
SELECT
    id, analysis_unit_id, track, stage, input_fingerprint, status,
    output_refs_json, result_checksum, private_result_json,
    completed_by_attempt_id, created_at, updated_at
FROM content_research_analysis_checkpoints_v37;
DROP TABLE content_research_analysis_checkpoints_v37;
CREATE UNIQUE INDEX idx_cr_analysis_checkpoint_completed_identity
    ON content_research_analysis_checkpoints(
        analysis_unit_id, track, stage, input_fingerprint
    ) WHERE status='completed';
CREATE UNIQUE INDEX idx_cr_analysis_checkpoint_failed_attempt_identity
    ON content_research_analysis_checkpoints(
        analysis_unit_id, completed_by_attempt_id, track, stage, input_fingerprint
    ) WHERE status='failed';
CREATE INDEX idx_cr_analysis_checkpoint_unit_track_stage
    ON content_research_analysis_checkpoints(analysis_unit_id, track, stage, status);
CREATE TRIGGER cr_trace_revision_analysis_checkpoint_insert
AFTER INSERT ON content_research_analysis_checkpoints BEGIN
    INSERT INTO content_research_trace_revisions(workflow_run_id, revision, updated_at)
    SELECT workflow_run_id, 1, CURRENT_TIMESTAMP
    FROM content_research_analysis_units WHERE id=NEW.analysis_unit_id
    ON CONFLICT(workflow_run_id) DO UPDATE SET revision=revision+1, updated_at=CURRENT_TIMESTAMP;
END;
"""


def _apply_0038(conn: sqlite3.Connection) -> None:
    conn.executescript(_V38_ATTEMPT_SCOPED_ANALYSIS_FAILURES_SQL)


_V39_SOURCE_OBSERVATIONS_SQL = """
CREATE TABLE content_research_source_observations (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    canonical_source_id TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    observation_fingerprint TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(canonical_source_id, workflow_run_id, observation_fingerprint)
);
CREATE INDEX idx_cr_source_observation_source_captured
    ON content_research_source_observations(canonical_source_id, captured_at, id);
ALTER TABLE content_research_directional_evidence_packets
    ADD COLUMN source_observation_id TEXT;
"""


def _apply_0039(conn: sqlite3.Connection) -> None:
    conn.executescript(_V39_SOURCE_OBSERVATIONS_SQL)


_TRACE_REVISION_WRITER_AUTHORITY_SQL = """
DROP TRIGGER IF EXISTS cr_trace_revision_transition_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_trace_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_observation_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_execution_unit_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_execution_unit_update;
DROP TRIGGER IF EXISTS cr_trace_revision_execution_fact_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_stage_checkpoint_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_stage_checkpoint_update;
DROP TRIGGER IF EXISTS cr_trace_revision_analysis_attempt_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_analysis_attempt_update;
DROP TRIGGER IF EXISTS cr_trace_revision_analysis_checkpoint_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_publication_insert;
DROP TRIGGER IF EXISTS cr_trace_revision_integrity_event_insert;
"""


def _activate_writer_owned_trace_revision(conn: sqlite3.Connection) -> None:
    for statement in (
        item.strip()
        for item in _TRACE_REVISION_WRITER_AUTHORITY_SQL.split(";")
        if item.strip()
    ):
        conn.execute(statement)


def _apply_0015(conn: sqlite3.Connection) -> None:
    conn.execute(_V15_LLM_CONFIGURATION_SQL)


def _apply_0010(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V10_COLUMNS)


def _apply_0011(conn: sqlite3.Connection) -> None:
    conn.executescript(_V11_DISPATCH_TABLE_SQL)


def _report_table_sql(table: str, columns: str) -> str:
    return f"CREATE TABLE {table} (id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, {columns}, payload_json TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{{}}', created_at TEXT NOT NULL)"


def _apply_0009(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE content_research_report_faithfulness_decisions")
    for table, columns in _V9_REPORT_TABLES.items():
        conn.execute(_report_table_sql(table, columns))
    conn.execute(
        "CREATE INDEX idx_cr_report_draft_snapshot ON content_research_report_drafts(workflow_run_id, governed_snapshot_id, governed_snapshot_version)"
    )
    conn.execute(
        "CREATE INDEX idx_cr_report_publication_snapshot ON content_research_report_publications(workflow_run_id, governed_snapshot_id, governed_snapshot_version)"
    )


def _checksum(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _expected_checksums(migration_0002_sql: str, legacy_checksum: str) -> dict[str, str]:
    """Return the immutable checksum set for a fully migrated F003 database."""
    return {
        "0001": legacy_checksum,
        "0002": hashlib.sha256(migration_0002_sql.encode("utf-8")).hexdigest(),
        "0003": _checksum(_V3_COLUMNS),
        "0004": _checksum(_V4_COLUMNS),
        "0005": _checksum(_TYPED_TABLES),
        "0006": _checksum(_V6_INDEXES),
        "0007": _checksum((_V7_COLUMNS, _V7_INDEXES)),
        "0008": _checksum(_V8_COLUMNS),
        "0009": _checksum(_V9_REPORT_TABLES),
        "0010": _checksum(_V10_COLUMNS),
        "0011": hashlib.sha256(_V11_DISPATCH_TABLE_SQL.encode("utf-8")).hexdigest(),
        "0012": _checksum(_V12_DISPATCH_LEASE_COLUMNS),
        "0013": _checksum((_V13_LEGACY_TABLES, _V13_LEGACY_SNAPSHOT_COLUMN)),
        "0014": _checksum(_V14_REPORT_TABLES),
        "0015": hashlib.sha256(_V15_LLM_CONFIGURATION_SQL.encode("utf-8")).hexdigest(),
        "0016": _checksum((_V16_SUPERSEDED_LITE_REPORT_TABLES, _V16_MARKETING_CONCLUSION_SQL)),
        "0017": hashlib.sha256(_V17_XHS_CREDENTIAL_SQL.encode("utf-8")).hexdigest(),
        "0018": hashlib.sha256(_V18_XHS_CREDENTIAL_STATUS_SQL.encode("utf-8")).hexdigest(),
        "0019": hashlib.sha256(_V19_SCOPE_CONTRACT_SQL.encode("utf-8")).hexdigest(),
        "0020": hashlib.sha256(_V20_SCOPE_COVERAGE_SQL.encode("utf-8")).hexdigest(),
        "0021": hashlib.sha256(_V21_SCOPE_DRAFT_SQL.encode("utf-8")).hexdigest(),
        "0022": hashlib.sha256(_V22_SCOPE_DRAFT_CONFIRMATION_SQL.encode("utf-8")).hexdigest(),
        "0023": hashlib.sha256(_V23_SCOPE_EXECUTION_AUTHORIZATION_SQL.encode("utf-8")).hexdigest(),
        "0024": hashlib.sha256(_V24_SCOPE_EXECUTION_CONTINUATION_SQL.encode("utf-8")).hexdigest(),
        "0025": hashlib.sha256(_V25_EXECUTION_UNITS_SQL.encode("utf-8")).hexdigest(),
        "0026": _checksum("execution_unit_alias_canonical_repair_v1"),
        "0027": _checksum("execution_decision_identity_contract_v1"),
        "0028": _checksum("minimal_execution_decision_identity_v1"),
        "0029": _checksum((_V29_EXECUTION_LINEAGE_COLUMNS, _V29_EXECUTION_LINEAGE_INDEXES)),
        "0030": _checksum((_V30_REPORT_LINEAGE_COLUMNS, _V30_REPORT_LINEAGE_INDEXES)),
        "0031": _checksum(_V31_REPORT_INTEGRITY_EVENT_STATEMENTS),
        "0032": _checksum(_V32_SCOPE_DRAFT_VERSION_COLUMNS),
        "0033": hashlib.sha256(_V33_LIFECYCLE_AUTHORITY_SQL.encode("utf-8")).hexdigest(),
        "0034": hashlib.sha256(_V34_MARKETING_ANALYSIS_IDENTITY_SQL.encode("utf-8")).hexdigest(),
        "0035": hashlib.sha256(_V35_MARKETING_ANALYSIS_JOB_SQL.encode("utf-8")).hexdigest(),
        "0036": hashlib.sha256(_V36_TRACE_REVISION_SQL.encode("utf-8")).hexdigest(),
        "0037": hashlib.sha256(_V37_ANALYSIS_AUTHORITY_SQL.encode("utf-8")).hexdigest(),
        "0038": hashlib.sha256(
            _V38_ATTEMPT_SCOPED_ANALYSIS_FAILURES_SQL.encode("utf-8")
        ).hexdigest(),
        "0039": hashlib.sha256(_V39_SOURCE_OBSERVATIONS_SQL.encode("utf-8")).hexdigest(),
        "0040": hashlib.sha256(
            _TRACE_REVISION_WRITER_AUTHORITY_SQL.encode("utf-8")
        ).hexdigest(),
    }


def _apply_migration(
    conn: sqlite3.Connection,
    *,
    version: str,
    name: str,
    checksum: str,
    apply: Callable[[], None],
) -> None:
    row = conn.execute(
        "SELECT checksum FROM content_research_schema_migrations WHERE version = ?", (version,)
    ).fetchone()
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
    expected_checksums = _expected_checksums(migration_0002_sql, legacy_checksum)
    # Store construction occurs on normal read paths too.  A concurrent formal
    # collection may briefly own the writer lock.  A current schema must only
    # read its migration ledger, never contend for that writer lock.
    with closing(open_migration_database(db_path, timeout=30)) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        migration_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'content_research_schema_migrations'"
        ).fetchone()
        if migration_table is not None:
            applied_checksums = dict(
                conn.execute("SELECT version, checksum FROM content_research_schema_migrations")
            )
            if all(
                applied_checksums.get(version) == checksum
                for version, checksum in expected_checksums.items()
            ):
                return

        conn.execute(
            "CREATE TABLE IF NOT EXISTS content_research_schema_migrations (version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        legacy = conn.execute(
            "SELECT checksum FROM content_research_schema_migrations WHERE version = '0001'"
        ).fetchone()
        if legacy is None:
            conn.commit()
            legacy_bootstrap(db_path)
            conn.execute(
                "INSERT INTO content_research_schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (
                    "0001",
                    "legacy_bootstrap",
                    legacy_checksum,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        elif legacy[0] != legacy_checksum:
            raise RuntimeError("content research migration 0001 checksum mismatch")

        conn.execute("BEGIN IMMEDIATE")
        try:

            def apply_0002() -> None:
                for index, statement in enumerate(
                    (item.strip() for item in migration_0002_sql.split(";") if item.strip()),
                    start=1,
                ):
                    conn.execute(statement)
                    if fail_after_statement == index:
                        raise RuntimeError("injected migration failure")

            _apply_migration(
                conn,
                version="0002",
                name="formal_research_contracts",
                checksum=expected_checksums["0002"],
                apply=apply_0002,
            )
            _apply_migration(
                conn,
                version="0003",
                name="formal_entity_columns",
                checksum=expected_checksums["0003"],
                apply=lambda: _apply_0003(conn),
            )
            _apply_migration(
                conn,
                version="0004",
                name="additional_entity_columns",
                checksum=expected_checksums["0004"],
                apply=lambda: _apply_0004(conn),
            )
            _apply_migration(
                conn,
                version="0005",
                name="replace_generic_entity_tables",
                checksum=expected_checksums["0005"],
                apply=lambda: _apply_0005(conn),
            )
            _apply_migration(
                conn,
                version="0006",
                name="role_identity_constraints",
                checksum=expected_checksums["0006"],
                apply=lambda: _apply_0006(conn),
            )
            _apply_migration(
                conn,
                version="0007",
                name="run_scoped_packet_read_model",
                checksum=expected_checksums["0007"],
                apply=lambda: _apply_0007(conn),
            )
            _apply_migration(
                conn,
                version="0008",
                name="recomputable_claim_candidates",
                checksum=expected_checksums["0008"],
                apply=lambda: _apply_0008(conn),
            )
            _apply_migration(
                conn,
                version="0009",
                name="append_only_report_versions",
                checksum=expected_checksums["0009"],
                apply=lambda: _apply_0009(conn),
            )
            _apply_migration(
                conn,
                version="0010",
                name="checkpoint_timing_boundaries",
                checksum=expected_checksums["0010"],
                apply=lambda: _apply_0010(conn),
            )
            _apply_migration(
                conn,
                version="0011",
                name="durable_formal_research_dispatch",
                checksum=expected_checksums["0011"],
                apply=lambda: _apply_0011(conn),
            )
            _apply_migration(
                conn,
                version="0012",
                name="dispatch_lease_fencing",
                checksum=expected_checksums["0012"],
                apply=lambda: _add_columns(conn, _V12_DISPATCH_LEASE_COLUMNS),
            )
            _apply_migration(
                conn,
                version="0013",
                name="remove_legacy_evidence_bundle_persistence",
                checksum=expected_checksums["0013"],
                apply=lambda: _apply_0013(conn),
            )
            _apply_migration(
                conn,
                version="0014",
                name="purge_pre_cutover_report_artifacts",
                checksum=expected_checksums["0014"],
                apply=lambda: _apply_0014(conn),
            )
            _apply_migration(
                conn,
                version="0015",
                name="workspace_scoped_llm_configurations",
                checksum=expected_checksums["0015"],
                apply=lambda: _apply_0015(conn),
            )
            _apply_migration(
                conn,
                version="0016",
                name="govern_lite_marketing_conclusions",
                checksum=expected_checksums["0016"],
                apply=lambda: _apply_0016(conn),
            )
            _apply_migration(
                conn,
                version="0017",
                name="local_xhs_credentials",
                checksum=expected_checksums["0017"],
                apply=lambda: _apply_0017(conn),
            )
            _apply_migration(
                conn,
                version="0018",
                name="local_xhs_credential_status",
                checksum=expected_checksums["0018"],
                apply=lambda: _apply_0018(conn),
            )
            _apply_migration(
                conn,
                version="0019",
                name="lite_scope_contract_audit",
                checksum=expected_checksums["0019"],
                apply=lambda: _apply_0019(conn),
            )
            _apply_migration(
                conn,
                version="0020",
                name="lite_scope_coverage_snapshots",
                checksum=expected_checksums["0020"],
                apply=lambda: _apply_0020(conn),
            )
            _apply_migration(
                conn,
                version="0021",
                name="lite_scope_drafts",
                checksum=expected_checksums["0021"],
                apply=lambda: _apply_0021(conn),
            )
            _apply_migration(
                conn,
                version="0022",
                name="lite_scope_draft_confirmations",
                checksum=expected_checksums["0022"],
                apply=lambda: _apply_0022(conn),
            )
            _apply_migration(
                conn,
                version="0023",
                name="lite_scope_execution_authorizations",
                checksum=expected_checksums["0023"],
                apply=lambda: _apply_0023(conn),
            )
            _apply_migration(
                conn,
                version="0024",
                name="scope_execution_continuation_lifecycle",
                checksum=expected_checksums["0024"],
                apply=lambda: _apply_0024(conn),
            )
            _apply_migration(
                conn,
                version="0025",
                name="scope_execution_units_and_facts",
                checksum=expected_checksums["0025"],
                apply=lambda: _apply_0025(conn),
            )
            _apply_migration(
                conn,
                version="0026",
                name="execution_unit_alias_canonical_repair",
                checksum=expected_checksums["0026"],
                apply=lambda: _apply_0026(conn),
            )
            _apply_migration(
                conn,
                version="0027",
                name="execution_decision_identity_contract",
                checksum=expected_checksums["0027"],
                apply=lambda: _apply_0026(conn),
            )
            _apply_migration(
                conn,
                version="0028",
                name="minimal_execution_decision_identity",
                checksum=expected_checksums["0028"],
                apply=lambda: _apply_0028(conn),
            )
            _apply_migration(
                conn,
                version="0029",
                name="execution_lineage_owned_evidence",
                checksum=expected_checksums["0029"],
                apply=lambda: _apply_0029(conn),
            )
            _apply_migration(
                conn,
                version="0030",
                name="report_execution_lineage",
                checksum=expected_checksums["0030"],
                apply=lambda: _apply_0030(conn),
            )
            _apply_migration(
                conn,
                version="0031",
                name="report_publication_integrity_events",
                checksum=expected_checksums["0031"],
                apply=lambda: _apply_0031(conn),
            )
            _apply_migration(
                conn,
                version="0032",
                name="version_scope_drafts_for_query_portfolios",
                checksum=expected_checksums["0032"],
                apply=lambda: _apply_0032(conn),
            )
            _apply_migration(
                conn,
                version="0033",
                name="content_research_lifecycle_authority",
                checksum=expected_checksums["0033"],
                apply=lambda: _apply_0033(conn),
            )
            _apply_migration(
                conn,
                version="0034",
                name="marketing_analysis_identity_skeleton",
                checksum=expected_checksums["0034"],
                apply=lambda: _apply_0034(conn),
            )
            _apply_migration(
                conn,
                version="0035",
                name="marketing_analysis_durable_jobs",
                checksum=expected_checksums["0035"],
                apply=lambda: _apply_0035(conn),
            )
            _apply_migration(
                conn,
                version="0036",
                name="content_research_trace_revision",
                checksum=expected_checksums["0036"],
                apply=lambda: _apply_0036(conn),
            )
            _apply_migration(
                conn,
                version="0037",
                name="marketing_analysis_explicit_authority",
                checksum=expected_checksums["0037"],
                apply=lambda: _apply_0037(conn),
            )
            _apply_migration(
                conn,
                version="0038",
                name="attempt_scoped_analysis_failures",
                checksum=expected_checksums["0038"],
                apply=lambda: _apply_0038(conn),
            )
            _apply_migration(
                conn,
                version="0039",
                name="versioned_source_observations",
                checksum=expected_checksums["0039"],
                apply=lambda: _apply_0039(conn),
            )
            _apply_migration(
                conn,
                version="0040",
                name="writer_owned_trace_revision",
                checksum=expected_checksums["0040"],
                apply=lambda: _activate_writer_owned_trace_revision(conn),
            )
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
