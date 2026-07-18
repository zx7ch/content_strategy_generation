"""SQLite schema bootstrap for Content Research persistence."""

from __future__ import annotations

import sqlite3


def _bootstrap_legacy_content_research_schema(db_path: str) -> None:
    """Create Content Research tables and indexes if they do not exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content_research_briefs (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_briefs_thread_status_created
                ON content_research_briefs(thread_id, status, created_at);

            CREATE TABLE IF NOT EXISTS content_research_plans (
                id TEXT PRIMARY KEY,
                brief_id TEXT NOT NULL,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_plans_brief_status_created
                ON content_research_plans(brief_id, status, created_at);

            CREATE TABLE IF NOT EXISTS content_research_directions (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_directions_plan_status_priority
                ON content_research_directions(plan_id, status, priority);

            CREATE TABLE IF NOT EXISTS content_research_subagent_tasks (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_id TEXT,
                direction_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_subagent_tasks_workflow_status_created
                ON content_research_subagent_tasks(workflow_run_id, status, created_at);

            CREATE TABLE IF NOT EXISTS content_research_traces (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_traces_workflow_started
                ON content_research_traces(workflow_run_id, started_at);

            CREATE TABLE IF NOT EXISTS content_research_observation_events (
                id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                sequence_no INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_research_observation_events_trace_sequence
                ON content_research_observation_events(trace_id, sequence_no);
            CREATE INDEX IF NOT EXISTS idx_content_research_observation_events_trace_created
                ON content_research_observation_events(trace_id, sequence_no);

            CREATE TABLE IF NOT EXISTS content_research_evidence_records (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                research_brief_id TEXT,
                research_plan_id TEXT,
                research_direction_id TEXT,
                subagent_task_id TEXT,
                trace_id TEXT,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_author_id TEXT,
                source_author_name TEXT,
                source_published_at TEXT,
                collected_at TEXT NOT NULL,
                title TEXT NOT NULL,
                text_excerpt TEXT NOT NULL,
                raw_content_ref TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                claim TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                language TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                retrieval_query TEXT NOT NULL,
                retrieval_rank INTEGER,
                retrieval_score REAL,
                normalized_payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_records_workflow_status_collected
                ON content_research_evidence_records(workflow_run_id, status, collected_at);
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_records_plan_status_collected
                ON content_research_evidence_records(research_plan_id, status, collected_at);
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_records_direction_status_collected
                ON content_research_evidence_records(research_direction_id, status, collected_at);
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_records_task_status_collected
                ON content_research_evidence_records(subagent_task_id, status, collected_at);
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_records_source_url
                ON content_research_evidence_records(source_url);
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_records_dedupe_key
                ON content_research_evidence_records(dedupe_key);

            CREATE TABLE IF NOT EXISTS content_research_evidence_lineage (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                evidence_record_id TEXT NOT NULL,
                research_brief_id TEXT,
                research_plan_id TEXT,
                research_direction_id TEXT,
                subagent_task_id TEXT,
                trace_id TEXT,
                parent_evidence_record_id TEXT,
                schema_version TEXT NOT NULL,
                transformation_type TEXT NOT NULL,
                transformation_version TEXT NOT NULL,
                lineage_payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_lineage_record_created
                ON content_research_evidence_lineage(evidence_record_id, created_at);

            CREATE TABLE IF NOT EXISTS content_research_evidence_bundles (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                research_brief_id TEXT,
                research_plan_id TEXT,
                research_direction_id TEXT,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                bundle_type TEXT NOT NULL,
                bundle_version TEXT NOT NULL,
                summary TEXT NOT NULL,
                coverage_json TEXT NOT NULL DEFAULT '{}',
                retrieval_metrics_json TEXT NOT NULL DEFAULT '{}',
                faithfulness_metrics_json TEXT NOT NULL DEFAULT '{}',
                cross_source_metrics_json TEXT NOT NULL DEFAULT '{}',
                contradiction_summary_json TEXT NOT NULL DEFAULT '{}',
                citation_coverage_json TEXT NOT NULL DEFAULT '{}',
                unsupported_claim_count INTEGER NOT NULL DEFAULT 0,
                missing_evidence_json TEXT NOT NULL DEFAULT '[]',
                priority_policy_id TEXT,
                evidence_boundary_policy_id TEXT,
                decision_card_json TEXT NOT NULL DEFAULT '{}',
                priority_json TEXT NOT NULL DEFAULT '{}',
                evidence_state TEXT NOT NULL DEFAULT 'signal',
                evidence_grade TEXT NOT NULL DEFAULT 'C',
                claim_scope_json TEXT NOT NULL DEFAULT '{}',
                next_action_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_bundles_direction_status_created
                ON content_research_evidence_bundles(research_direction_id, status, created_at);

            CREATE TABLE IF NOT EXISTS content_research_evidence_bundle_items (
                id TEXT PRIMARY KEY,
                bundle_id TEXT NOT NULL,
                evidence_record_id TEXT,
                role TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                schema_version TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_evidence_bundle_items_bundle_order
                ON content_research_evidence_bundle_items(bundle_id, sort_order);

            CREATE TABLE IF NOT EXISTS content_research_result_snapshots (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                research_brief_id TEXT,
                research_plan_id TEXT,
                schema_version TEXT NOT NULL,
                snapshot_version TEXT NOT NULL,
                result_type TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                executive_summary TEXT NOT NULL,
                findings_json TEXT NOT NULL DEFAULT '[]',
                recommendations_json TEXT NOT NULL DEFAULT '[]',
                evidence_bundle_ids_json TEXT NOT NULL DEFAULT '[]',
                claim_count INTEGER NOT NULL DEFAULT 0,
                supported_claim_count INTEGER NOT NULL DEFAULT 0,
                unsupported_claim_count INTEGER NOT NULL DEFAULT 0,
                citation_coverage_score REAL,
                faithfulness_score REAL,
                answer_relevancy_score REAL,
                derivation_completeness_score REAL,
                evidence_boundary_calibration_score REAL,
                decision_summary_json TEXT NOT NULL DEFAULT '{}',
                decision_cards_json TEXT NOT NULL DEFAULT '[]',
                priority_summary_json TEXT NOT NULL DEFAULT '{}',
                evidence_boundary_summary_json TEXT NOT NULL DEFAULT '{}',
                limitations_json TEXT NOT NULL DEFAULT '[]',
                abstentions_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_research_result_snapshots_workflow_created
                ON content_research_result_snapshots(workflow_run_id, created_at);

            CREATE TABLE IF NOT EXISTS content_research_human_decisions (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                decision_request_id TEXT NOT NULL,
                decision_status TEXT NOT NULL,
                decision_payload_json TEXT NOT NULL DEFAULT '{}',
                rationale TEXT NOT NULL DEFAULT '',
                created_by_type TEXT NOT NULL,
                created_by_id TEXT,
                research_brief_id TEXT,
                research_plan_id TEXT,
                research_result_snapshot_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_research_human_decisions_idempotency
                ON content_research_human_decisions(workflow_run_id, target_type, target_id, decision_request_id);
            CREATE INDEX IF NOT EXISTS idx_content_research_human_decisions_workflow_created
                ON content_research_human_decisions(workflow_run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_content_research_human_decisions_target_created
                ON content_research_human_decisions(workflow_run_id, target_type, target_id, created_at);
            """
        )


def bootstrap_content_research_schema(db_path: str) -> None:
    """Apply ordered Content Research schema migrations."""
    from app.content_research.migrations import apply_content_research_migrations

    apply_content_research_migrations(db_path, _bootstrap_legacy_content_research_schema)
