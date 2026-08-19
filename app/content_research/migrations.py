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


def _apply_0025(conn: sqlite3.Connection) -> None:
    """Backfill legacy authorization rows as aliases without changing Scope meaning."""
    # Do not use sqlite3.executescript here: it commits any active transaction
    # before executing, which would strand schema changes if backfill fails.
    for statement in _V25_EXECUTION_UNITS_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)
    rows = conn.execute(
        """SELECT authorization.id, authorization.workflow_run_id, authorization.scope_contract_id,
                  authorization.coverage_snapshot_id, authorization.resolution,
                  authorization.created_at, continuation.operation, continuation.state,
                  continuation.lease_owner, continuation.lease_token,
                  continuation.lease_expires_at, continuation.supplementary_queries_json,
                  snapshot.scope_contract_id
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
        ) = row
        operation = operation or (
            "limited_report"
            if resolution == "generate_limited_report"
            else "supplementary_collection"
        )
        queries = json.loads(supplementary_queries_json or "[]")
        identity = json.dumps(
            {"coverage_snapshot_id": coverage_snapshot_id,
             "source_scope_contract_id": source_scope_contract_id,
             "resulting_scope_contract_id": scope_contract_id,
             "resolution": resolution, "operation": operation, "supplementary_queries": queries},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        decision_fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        unit_id = "seu_" + decision_fingerprint[:24]
        state = continuation_state or "pending"
        now = created_at or datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO content_research_scope_execution_units
               (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                coverage_snapshot_id, resolution, operation, state, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                unit_id,
                decision_fingerprint,
                workflow_run_id,
                scope_contract_id,
                coverage_snapshot_id,
                resolution,
                operation,
                state if state in {"pending", "running", "completed", "failed"} else "failed",
                now,
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
    rows = conn.execute(
        """SELECT unit.id, authorization.coverage_snapshot_id, snapshot.scope_contract_id,
                  authorization.scope_contract_id, authorization.resolution, continuation.operation,
                  continuation.supplementary_queries_json
           FROM content_research_scope_execution_units AS unit
           JOIN content_research_scope_execution_authorizations AS authorization
             ON authorization.execution_unit_id=unit.id
           JOIN content_research_scope_coverage_snapshots AS snapshot
             ON snapshot.id=authorization.coverage_snapshot_id
           LEFT JOIN content_research_scope_execution_continuations AS continuation
             ON continuation.authorization_id=authorization.id"""
    ).fetchall()
    for unit_id, snapshot_id, source_scope_id, resulting_scope_id, resolution, operation, raw_queries in rows:
        identity = json.dumps(
            {"coverage_snapshot_id": snapshot_id, "source_scope_contract_id": source_scope_id,
             "resulting_scope_contract_id": resulting_scope_id, "resolution": resolution,
             "operation": operation or ("limited_report" if resolution == "generate_limited_report" else "supplementary_collection"),
             "supplementary_queries": json.loads(raw_queries or "[]")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        conn.execute(
            "UPDATE content_research_scope_execution_units SET decision_fingerprint=? WHERE id=?",
            (hashlib.sha256(identity.encode()).hexdigest(), unit_id),
        )


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
    with sqlite3.connect(db_path, timeout=30) as conn:
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
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
