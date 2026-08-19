from __future__ import annotations

import sqlite3
import threading

import pytest

import app.content_research.migrations as migrations
from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.migrations import apply_content_research_migrations
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def test_migration_0015_creates_scoped_configuration_table(tmp_path):
    db_path = str(tmp_path / "content_research.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(content_research_llm_configurations)")
        }
        versions = {
            row[0]
            for row in conn.execute("SELECT version FROM content_research_schema_migrations")
        }
    assert {
        "workspace_id",
        "user_id",
        "base_url",
        "model",
        "api_key",
        "validation_status",
    } <= columns
    assert "0015" in versions


def test_current_schema_bootstrap_does_not_wait_for_active_writer(tmp_path):
    """A read-path store construction must not reacquire a migration write lock."""
    db_path = tmp_path / "content_research.db"
    bootstrap_content_research_schema(str(db_path))

    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")
    completed = threading.Event()
    failure: list[BaseException] = []

    def bootstrap_again() -> None:
        try:
            bootstrap_content_research_schema(str(db_path))
        except BaseException as exc:  # pragma: no cover - assertion re-raises it below
            failure.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=bootstrap_again)
    worker.start()
    try:
        assert completed.wait(timeout=0.5), "current schemas must not wait for a migration write lock"
        assert failure == []
    finally:
        writer.rollback()
        writer.close()
        worker.join(timeout=1)


def test_migration_backfills_historical_limited_report_authority_and_continuation(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "legacy-limited-report.db")
    store = SQLiteContentResearchStore(db_path)
    contract = build_scope_contract(
        workflow_run_id="run_legacy_limited",
        research_plan_id="rp_legacy_limited",
        version=1,
        constraints=(ScopeConstraint("core_object", "核心对象", "衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("衬衫", "衬衫", ("衬衫",)),),
    )
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_legacy_limited",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={"core_object": {"matched_candidate_count": 0, "required": True}},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(snapshot)
    event = ScopeAuditEvent(
        id="sae_legacy_limited",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="coverage_resolved",
        payload={
            "schema_version": "content_research_scope_audit_event_v1",
            "coverage_snapshot_id": snapshot.id,
            "resolution": "generate_limited_report",
            "report_mode": "limited",
        },
    )
    store.append_scope_audit_event(event)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version = '0024'")
        conn.execute("DROP TABLE IF EXISTS content_research_scope_execution_continuations")
        conn.execute("DELETE FROM content_research_scope_execution_authorizations")
        conn.execute(
            "CREATE TABLE legacy_coverage AS SELECT id, workflow_run_id, scope_contract_id, "
            "scope_contract_version, state, constraint_counts_json, unmet_constraint_ids_json, "
            "created_at FROM content_research_scope_coverage_snapshots"
        )
        conn.execute("DROP TABLE content_research_scope_coverage_snapshots")
        conn.execute(
            "CREATE TABLE content_research_scope_coverage_snapshots ("
            "id TEXT PRIMARY KEY, workflow_run_id TEXT NOT NULL, "
            "scope_contract_id TEXT NOT NULL UNIQUE, scope_contract_version INTEGER NOT NULL, "
            "state TEXT NOT NULL, constraint_counts_json TEXT NOT NULL, "
            "unmet_constraint_ids_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO content_research_scope_coverage_snapshots SELECT * FROM legacy_coverage"
        )
        conn.execute("DROP TABLE legacy_coverage")

    bootstrap_content_research_schema(db_path)
    migrated = SQLiteContentResearchStore(db_path)
    authorization = migrated.list_scope_execution_authorizations(
        contract.workflow_run_id
    )[0]
    continuation = migrated.list_scope_execution_continuations(contract.workflow_run_id)[0]
    assert authorization.resolution == "generate_limited_report"
    assert authorization.state == "authorized_limited_report"
    assert authorization.execution_revision == 2
    assert continuation.authorization_id == authorization.id
    assert continuation.operation == "limited_report"
    assert continuation.state == "pending"

    replay = migrated.resolve_coverage_and_authorize_execution_atomically(
        snapshot=migrated.get_coverage_snapshot(contract.workflow_run_id, version=1),
        authorization=ScopeExecutionAuthorization(
            id="sea_replay_candidate",
            workflow_run_id=contract.workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=contract.version,
            coverage_snapshot_id=snapshot.id,
            resolution="generate_limited_report",
            execution_revision=2,
            state="authorized_limited_report",
        ),
        continuation=ScopeExecutionContinuation(
            id="sec_replay_candidate",
            authorization_id="sea_replay_candidate",
            workflow_run_id=contract.workflow_run_id,
            execution_revision=2,
            operation="limited_report",
            supplementary_queries=(),
            state="pending",
        ),
        event=event,
    )
    assert replay[2] == authorization
    assert replay[3] == continuation
    assert replay[4] is False


def test_execution_unit_backfill_rolls_back_then_retries(tmp_path, monkeypatch) -> None:
    """A post-backfill failure must leave neither 0025 schema nor ledger state behind."""
    db_path = str(tmp_path / "execution-unit-backfill-retry.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version IN ('0025', '0026')")
        conn.execute("DROP TABLE content_research_execution_facts")
        conn.execute("DROP TABLE content_research_scope_execution_attempts")
        conn.execute("DROP TABLE content_research_scope_execution_units")
        conn.execute("ALTER TABLE content_research_scope_execution_authorizations DROP COLUMN execution_unit_id")
        conn.execute("ALTER TABLE content_research_scope_execution_continuations DROP COLUMN execution_unit_id")

    original = migrations._apply_0025

    def fail_after_backfill(conn):
        original(conn)
        raise RuntimeError("injected 0025 backfill failure")

    monkeypatch.setattr(migrations, "_apply_0025", fail_after_backfill)
    with pytest.raises(RuntimeError, match="injected 0025"):
        apply_content_research_migrations(db_path, bootstrap_content_research_schema)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_research_scope_execution_units'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM content_research_schema_migrations WHERE version='0025'"
        ).fetchone() is None

    monkeypatch.setattr(migrations, "_apply_0025", original)
    apply_content_research_migrations(db_path, bootstrap_content_research_schema)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM content_research_schema_migrations WHERE version='0026'"
        ).fetchone() is not None


def test_execution_unit_repair_rolls_back_seeded_legacy_alias_then_retries(tmp_path, monkeypatch) -> None:
    """0026 must atomically repair existing aliases, not merely write its ledger row."""
    db_path = str(tmp_path / "execution-unit-repair-retry.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        now = "2026-08-19T00:00:00+00:00"
        conn.execute(
            "INSERT INTO content_research_scope_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("rsc_source", "run-repair", "rp-repair", 1, "v", "[]", "[]", now),
        )
        conn.execute(
            "INSERT INTO content_research_scope_coverage_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scv-repair", "run-repair", "rsc_source", 1, 1, None, None, "awaiting_scope_decision", "{}", "[]", now),
        )
        conn.execute(
            "INSERT INTO content_research_scope_execution_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seu_legacy", "legacy-fingerprint", "run-repair", "rsc_source", "scv-repair", "generate_limited_report", "limited_report", "pending", now),
        )
        conn.execute(
            "INSERT INTO content_research_scope_execution_authorizations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sea-repair", "run-repair", "rsc_source", 1, "scv-repair", "generate_limited_report", 2, "authorized_limited_report", now, "seu_legacy"),
        )
        conn.execute(
            "INSERT INTO content_research_scope_execution_continuations (id, authorization_id, workflow_run_id, execution_revision, operation, supplementary_queries_json, state, created_at, updated_at, execution_unit_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sec-repair", "sea-repair", "run-repair", 2, "limited_report", "[]", "pending", now, now, "seu_legacy"),
        )
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version='0026'")

    original = migrations._apply_0026
    def fail_after_repair(conn):
        original(conn)
        raise RuntimeError("injected 0026 repair failure")
    monkeypatch.setattr(migrations, "_apply_0026", fail_after_repair)
    with pytest.raises(RuntimeError, match="injected 0026"):
        apply_content_research_migrations(db_path, bootstrap_content_research_schema)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT decision_fingerprint FROM content_research_scope_execution_units").fetchone()[0] == "legacy-fingerprint"
        assert conn.execute("SELECT 1 FROM content_research_schema_migrations WHERE version='0026'").fetchone() is None
    monkeypatch.setattr(migrations, "_apply_0026", original)
    apply_content_research_migrations(db_path, bootstrap_content_research_schema)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT decision_fingerprint FROM content_research_scope_execution_units").fetchone()[0] != "legacy-fingerprint"
