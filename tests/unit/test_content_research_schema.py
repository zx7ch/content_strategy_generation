from __future__ import annotations

import inspect
import sqlite3

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.stores import base
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def test_content_research_bootstrap_creates_p0_tables(tmp_path):
    db_path = tmp_path / "content_research.db"

    bootstrap_content_research_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'content_research_%'"
            )
        }

    assert {
        "content_research_briefs",
        "content_research_evidence_lineage",
        "content_research_evidence_records",
        "content_research_result_snapshots",
        "content_research_human_decisions",
        "content_research_plans",
        "content_research_directions",
        "content_research_subagent_tasks",
        "content_research_traces",
        "content_research_observation_events",
    }.issubset(tables)
    assert {
        "content_research_schema_migrations",
        "content_research_run_policy_snapshots",
        "content_research_sample_policies",
        "content_research_direction_contracts",
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
    }.issubset(tables)
    assert not any("_".join(("evidence", "bundle")) in table for table in tables)


def test_content_research_bootstrap_creates_required_indexes(tmp_path):
    db_path = tmp_path / "content_research.db"

    bootstrap_content_research_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_content_research_%'"
            )
        }

    assert {
        "idx_content_research_briefs_thread_status_created",
        "idx_content_research_plans_brief_status_created",
        "idx_content_research_directions_plan_status_priority",
        "idx_content_research_subagent_tasks_workflow_status_created",
        "idx_content_research_traces_workflow_started",
        "idx_content_research_observation_events_trace_sequence",
        "idx_content_research_evidence_records_workflow_status_collected",
        "idx_content_research_evidence_records_plan_status_collected",
        "idx_content_research_evidence_records_direction_status_collected",
        "idx_content_research_evidence_records_task_status_collected",
        "idx_content_research_evidence_records_source_url",
        "idx_content_research_evidence_records_dedupe_key",
        "idx_content_research_evidence_lineage_record_created",
        "idx_content_research_result_snapshots_workflow_created",
        "idx_content_research_human_decisions_idempotency",
        "idx_content_research_human_decisions_workflow_created",
        "idx_content_research_human_decisions_target_created",
    }.issubset(indexes)
    assert not any("_".join(("evidence", "bundle")) in index for index in indexes)


def test_store_protocol_and_sqlite_adapter_do_not_expose_cloud_api():
    protocol_names = {
        name
        for name, value in inspect.getmembers(base.ContentResearchStore)
        if callable(value) and not name.startswith("_")
    }

    assert "connect" not in protocol_names
    assert "sync_to_cloud" not in protocol_names
    assert "postgres" not in inspect.getsource(SQLiteContentResearchStore).lower()
    assert "cloud" not in inspect.getsource(SQLiteContentResearchStore).lower()
