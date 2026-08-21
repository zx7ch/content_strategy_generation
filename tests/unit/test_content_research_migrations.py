from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest

import app.content_research.migrations as migrations
from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.execution_decision_identity import build_execution_decision_identity
from app.content_research.migrations import apply_content_research_migrations
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    ClaimCandidateRecord,
    CoverageManifest,
    DirectionalEvidencePacketRecord,
    StageCheckpointRecord,
)
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


def test_migration_0031_creates_append_only_report_integrity_events(tmp_path):
    db_path = str(tmp_path / "report-integrity-events.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(content_research_report_integrity_events)"
            )
        }
        migration = conn.execute(
            "SELECT name FROM content_research_schema_migrations WHERE version='0031'"
        ).fetchone()
    assert columns == {
        "id",
        "publication_id",
        "workflow_run_id",
        "event_type",
        "reason_code",
        "recovery_guidance",
        "created_at",
    }
    assert migration == ("report_publication_integrity_events",)


def test_migration_0031_rolls_back_partial_integrity_schema_on_failure(
    tmp_path,
):
    db_path = str(tmp_path / "report-integrity-events-rollback.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE content_research_report_integrity_events")
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version='0031'")
        conn.execute("CREATE TABLE integrity_index_collision (id TEXT)")
        conn.execute(
            "CREATE INDEX idx_cr_report_integrity_publication_created "
            "ON integrity_index_collision(id)"
        )

    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        apply_content_research_migrations(db_path, bootstrap_content_research_schema)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='content_research_report_integrity_events'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM content_research_schema_migrations WHERE version='0031'"
        ).fetchone() is None


def test_migration_0029_leaves_legacy_execution_evidence_unowned_and_manifest_fenced(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "legacy-0029-lineage.db")
    store = SQLiteContentResearchStore(db_path)
    contract = build_scope_contract(
        workflow_run_id="run_legacy_0029",
        research_plan_id="rp_legacy_0029",
        version=1,
        constraints=(ScopeConstraint("core_object", "核心对象", "衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("衬衫", "衬衫", ("衬衫",)),),
    )
    store.save_scope_contract(contract)
    store.save_canonical_source(
        CanonicalSourceRecord(
            "cs_legacy_0029",
            "v1",
            {},
            platform="xhs",
            platform_source_kind="note",
            platform_source_id="legacy-0029",
        )
    )
    store.save_directional_evidence_packet(
        DirectionalEvidencePacketRecord(
            "dep_legacy_0029",
            "v1",
            {
                "field_projection": {
                    "content_text": "legacy claim",
                    "source_url": "https://example/legacy-0029",
                },
                "field_availability": {"content_text": "present"},
                "retrieval_context": {},
            },
            workflow_run_id=contract.workflow_run_id,
            research_direction_id="product_marketing",
            canonical_source_id="cs_legacy_0029",
            field_projection_hash="legacy-hash",
        )
    )
    store.save_claim_candidate(
        ClaimCandidateRecord(
            "cc_legacy_0029",
            "v1",
            {
                "quote_refs": [
                    {
                        "field_path": "content_text",
                        "quote": "legacy claim",
                        "text_start": 0,
                        "text_end": 12,
                        "source_text_hash": hashlib.sha256(
                            b"legacy claim"
                        ).hexdigest(),
                        "source_url": "https://example/legacy-0029",
                    }
                ]
            },
            workflow_run_id=contract.workflow_run_id,
            research_direction_id="product_marketing",
            evidence_packet_id="dep_legacy_0029",
            statement="legacy claim",
            intent_id="value",
            claim_type="observation",
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            "scp_legacy_0029",
            "v1",
            {},
            workflow_run_id=contract.workflow_run_id,
            subagent_task_id="task_legacy_0029",
            stage_name="collect",
            input_fingerprint="legacy-0029",
            status="completed",
        )
    )
    store.save_coverage_snapshot(
        CoverageSnapshot(
            id="scv_legacy_0029",
            workflow_run_id=contract.workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=1,
            state="satisfied",
            constraint_counts={},
            unmet_constraint_ids=(),
        )
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version='0029'")
        for index in (
            "idx_cr_packet_execution_lineage",
            "idx_cr_candidate_execution_lineage",
            "idx_cr_checkpoint_execution_lineage",
        ):
            conn.execute(f"DROP INDEX {index}")
        for table, columns in migrations._V29_EXECUTION_LINEAGE_COLUMNS.items():
            for definition in reversed(columns):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {definition.split()[0]}")

    bootstrap_content_research_schema(db_path)
    migrated = SQLiteContentResearchStore(db_path)
    packet = migrated.get_typed_record(
        DirectionalEvidencePacketRecord, "dep_legacy_0029"
    )
    candidate = migrated.get_typed_record(ClaimCandidateRecord, "cc_legacy_0029")
    checkpoint = migrated.get_typed_record(StageCheckpointRecord, "scp_legacy_0029")
    coverage = migrated.get_coverage_snapshot(contract.workflow_run_id, version=1)
    assert packet is not None and candidate is not None and checkpoint is not None
    assert (packet.scope_contract_id, packet.execution_unit_id, packet.attempt_no) == (
        None,
        None,
        0,
    )
    assert (candidate.scope_contract_id, candidate.execution_unit_id, candidate.attempt_no) == (
        None,
        None,
        0,
    )
    assert (checkpoint.scope_contract_id, checkpoint.execution_unit_id, checkpoint.attempt_no) == (
        None,
        None,
        0,
    )
    assert coverage is not None and coverage.manifest is None
    with sqlite3.connect(db_path) as conn:
        versions = {
            row[0]
            for row in conn.execute(
                "SELECT version FROM content_research_schema_migrations WHERE version='0029'"
            )
        }
        indexes = {
            row[1]
            for table in (
                "content_research_directional_evidence_packets",
                "content_research_claim_candidates",
                "content_research_stage_checkpoints",
            )
            for row in conn.execute(f"PRAGMA index_list({table})")
        }
    assert versions == {"0029"}
    assert {
        "idx_cr_packet_execution_lineage",
        "idx_cr_candidate_execution_lineage",
        "idx_cr_checkpoint_execution_lineage",
    } <= indexes

    with pytest.raises(ValueError, match="evidence ownership mismatch"):
        migrated.save_coverage_snapshot(
            CoverageSnapshot(
                id="scv_new_manifest_0029",
                workflow_run_id=contract.workflow_run_id,
                scope_contract_id=contract.id,
                scope_contract_version=1,
                execution_revision=2,
                state="satisfied",
                constraint_counts={},
                unmet_constraint_ids=(),
                manifest=CoverageManifest(
                    workflow_run_id=contract.workflow_run_id,
                    scope_contract_id=contract.id,
                    execution_unit_id=None,
                    attempt_no=0,
                    execution_revision=2,
                    packet_ids=("dep_legacy_0029",),
                ),
            )
        )


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
        conn.execute(
            "DELETE FROM content_research_schema_migrations "
            "WHERE version IN ('0024', '0025', '0026', '0027')"
        )
        conn.execute("DROP TABLE content_research_execution_facts")
        conn.execute("DROP TABLE content_research_scope_execution_attempts")
        conn.execute("DROP TABLE content_research_scope_execution_units")
        conn.execute("DROP TABLE IF EXISTS content_research_scope_execution_continuations")
        conn.execute("DELETE FROM content_research_scope_execution_authorizations")
        conn.execute(
            "ALTER TABLE content_research_scope_execution_authorizations DROP COLUMN execution_unit_id"
        )
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


def test_forward_migration_repairs_an_already_applied_pre_identity_0026_schema(tmp_path) -> None:
    """A database that already recorded old 0025/0026 must still gain the identity contract."""
    db_path = str(tmp_path / "execution-unit-forward-identity.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version='0027'")
        conn.execute("DROP INDEX idx_cr_execution_unit_canonical_fingerprint")
        conn.execute("DROP INDEX idx_cr_execution_unit_legacy_authorization")
        for column in (
            "legacy_authorization_id",
            "identity_state",
            "identity_json",
            "identity_schema",
        ):
            conn.execute(
                f"ALTER TABLE content_research_scope_execution_units DROP COLUMN {column}"
            )

    apply_content_research_migrations(db_path, bootstrap_content_research_schema)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(content_research_scope_execution_units)"
            )
        }
        assert conn.execute(
            "SELECT 1 FROM content_research_schema_migrations WHERE version='0027'"
        ).fetchone() is not None
    assert {
        "identity_schema",
        "identity_json",
        "identity_state",
        "legacy_authorization_id",
    } <= columns


def test_forward_migration_removes_derived_operation_from_persisted_identity(tmp_path) -> None:
    db_path = str(tmp_path / "execution-identity-minimal-forward.db")
    store = SQLiteContentResearchStore(db_path)
    contract = build_scope_contract(
        workflow_run_id="run-minimal-forward",
        research_plan_id="rp-minimal-forward",
        version=1,
        constraints=(ScopeConstraint("core_object", "核心对象", "衬衫", "required"),),
        query_groups=(ScopeQueryGroupInput("衬衫", "衬衫"),),
    )
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv-minimal-forward",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(snapshot)
    expected = build_execution_decision_identity(
        coverage_snapshot_id=snapshot.id,
        source_scope_contract_id=contract.id,
        resulting_scope_contract_id=contract.id,
        resolution="generate_limited_report",
        target_constraint_id=None,
        supplementary_queries=(),
    )
    historical_payload = {**expected.payload, "operation": "limited_report"}
    historical_json = json.dumps(
        historical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    historical_digest = hashlib.sha256(historical_json.encode("utf-8")).hexdigest()
    historical_unit_id = "seu_" + historical_digest[:24]
    now = "2026-08-19T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version='0028'")
        conn.execute(
            """INSERT INTO content_research_scope_execution_units
               (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                coverage_snapshot_id, resolution, operation, state, created_at,
                identity_schema, identity_json, identity_state)
               VALUES (?, ?, ?, ?, ?, 'generate_limited_report', 'limited_report',
                       'pending', ?, 'execution_decision_identity_v1', ?, 'canonical')""",
            (
                historical_unit_id,
                historical_digest,
                contract.workflow_run_id,
                contract.id,
                snapshot.id,
                now,
                historical_json,
            ),
        )
        conn.execute(
            """INSERT INTO content_research_scope_execution_attempts
               (execution_unit_id, attempt_no, state, created_at)
               VALUES (?, 0, 'pending', ?)""",
            (historical_unit_id, now),
        )
        conn.execute(
            """INSERT INTO content_research_execution_facts
               (execution_unit_id, attempt_no, sequence_no, kind, payload_json, created_at)
               VALUES (?, 0, 1, 'decision_accepted', ?, ?)""",
            (
                historical_unit_id,
                json.dumps({"decision": historical_payload}, ensure_ascii=False),
                now,
            ),
        )

    apply_content_research_migrations(db_path, bootstrap_content_research_schema)

    migrated = SQLiteContentResearchStore(db_path)
    unit = migrated.get_scope_execution_unit(expected.execution_unit_id)
    assert unit is not None
    assert unit.identity_json == expected.canonical_json
    assert "operation" not in json.loads(unit.identity_json)
    assert [fact.execution_unit_id for fact in migrated.execution_trace(unit.id)] == [unit.id]
    assert "operation" not in migrated.execution_trace(unit.id)[0].payload["decision"]
    replay, created = migrated.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot,
        decision={"resolution": "generate_limited_report"},
    )
    assert replay.id == expected.execution_unit_id
    assert created is False


def test_execution_fingerprint_uniqueness_applies_only_to_canonical_identities(tmp_path) -> None:
    """Incomplete aliases may share an unknowable decision fingerprint without collapsing."""
    db_path = str(tmp_path / "execution-unit-partial-unique.db")
    bootstrap_content_research_schema(db_path)
    now = "2026-08-19T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        for suffix in ("a", "b"):
            conn.execute(
                """INSERT INTO content_research_scope_execution_units
                   (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                    coverage_snapshot_id, resolution, operation, state, created_at,
                    identity_schema, identity_json, identity_state, legacy_authorization_id)
                   VALUES (?, 'unknown-legacy-decision', 'run-partial', 'rsc-partial', ?,
                           'expand_required_constraint', 'supplementary_collection', 'failed', ?,
                           'execution_decision_identity_v1', '{}',
                           'legacy_identity_incomplete', ?)""",
                (f"seu-legacy-{suffix}", f"scv-partial-{suffix}", now, f"sea-partial-{suffix}"),
            )
        conn.execute(
            """INSERT INTO content_research_scope_execution_units
               (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                coverage_snapshot_id, resolution, operation, state, created_at,
                identity_schema, identity_json, identity_state)
               VALUES ('seu-canonical-a', 'canonical-duplicate', 'run-partial',
                       'rsc-partial', 'scv-canonical-a', 'generate_limited_report',
                       'limited_report', 'pending', ?, 'execution_decision_identity_v1',
                       '{}', 'canonical')""",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO content_research_scope_execution_units
                   (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                    coverage_snapshot_id, resolution, operation, state, created_at,
                    identity_schema, identity_json, identity_state)
                   VALUES ('seu-canonical-b', 'canonical-duplicate', 'run-partial',
                           'rsc-partial', 'scv-canonical-b', 'generate_limited_report',
                           'limited_report', 'pending', ?, 'execution_decision_identity_v1',
                           '{}', 'canonical')""",
                (now,),
            )


def test_execution_unit_repair_rolls_back_seeded_legacy_alias_then_retries(tmp_path, monkeypatch) -> None:
    """0026 must atomically repair a semantic-relaxation alias and its ledger row."""
    db_path = str(tmp_path / "execution-unit-repair-retry.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        now = "2026-08-19T00:00:00+00:00"
        conn.execute(
            "INSERT INTO content_research_scope_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("rsc_source", "run-repair", "rp-repair", 1, "v", "[]", "[]", now),
        )
        conn.execute(
            "INSERT INTO content_research_scope_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("rsc_relaxed", "run-repair", "rp-repair", 2, "v", "[]", "[]", now),
        )
        conn.execute(
            """INSERT INTO content_research_scope_coverage_snapshots
               (id, workflow_run_id, scope_contract_id, scope_contract_version,
                execution_revision, execution_authorization_id,
                source_coverage_snapshot_id, state, constraint_counts_json,
                unmet_constraint_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("scv-repair", "run-repair", "rsc_source", 1, 1, None, None, "awaiting_scope_decision", "{}", '["season"]', now),
        )
        conn.execute(
            "INSERT INTO content_research_scope_audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sae-repair",
                "run-repair",
                "rsc_relaxed",
                2,
                "coverage_resolved",
                '{"schema_version":"content_research_scope_audit_event_v1","coverage_snapshot_id":"scv-repair","resolution":"relax_constraint","constraint_id":"season"}',
                "{}",
                now,
            ),
        )
        conn.execute(
            """INSERT INTO content_research_scope_execution_units
               (id, decision_fingerprint, workflow_run_id, scope_contract_id,
                coverage_snapshot_id, resolution, operation, state, created_at,
                identity_schema, identity_json, identity_state, legacy_authorization_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "seu_legacy",
                "legacy-fingerprint",
                "run-repair",
                "rsc_relaxed",
                "scv-repair",
                "relax_constraint",
                "supplementary_collection",
                "pending",
                now,
                "execution_decision_identity_v1",
                "{}",
                "legacy_identity_incomplete",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO content_research_scope_execution_authorizations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sea-repair", "run-repair", "rsc_relaxed", 2, "scv-repair", "relax_constraint", 1, "authorized_collection", now, "seu_legacy"),
        )
        conn.execute(
            "INSERT INTO content_research_scope_execution_continuations (id, authorization_id, workflow_run_id, execution_revision, operation, supplementary_queries_json, state, created_at, updated_at, execution_unit_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sec-repair", "sea-repair", "run-repair", 1, "supplementary_collection", "[]", "pending", now, now, "seu_legacy"),
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
    expected = build_execution_decision_identity(
        coverage_snapshot_id="scv-repair",
        source_scope_contract_id="rsc_source",
        resulting_scope_contract_id="rsc_relaxed",
        resolution="relax_constraint",
        target_constraint_id="season",
        supplementary_queries=(),
    )
    with sqlite3.connect(db_path) as conn:
        repaired = conn.execute(
            "SELECT decision_fingerprint, identity_json, identity_state "
            "FROM content_research_scope_execution_units"
        ).fetchone()
        assert repaired == (
            expected.decision_fingerprint,
            expected.canonical_json,
            "canonical",
        )


@pytest.mark.parametrize(
    ("resolution", "target_constraint_id", "queries", "resulting_scope_id", "expected_state"),
    [
        ("generate_limited_report", None, (), "rsc-source", "canonical"),
        ("expand_required_constraint", "season", ("夏季 防晒 衬衫",), "rsc-source", "canonical"),
        ("relax_constraint", "season", (), "rsc-relaxed", "canonical"),
        (
            "expand_required_constraint",
            None,
            ("夏季 防晒 衬衫",),
            "rsc-source",
            "legacy_identity_incomplete",
        ),
    ],
)
def test_migration_backfills_legacy_decision_identity_matrix(
    tmp_path,
    resolution,
    target_constraint_id,
    queries,
    resulting_scope_id,
    expected_state,
) -> None:
    """0025/0026 must recover persisted targets, including semantic relaxation, or fence replay."""
    db_path = str(tmp_path / f"legacy-{resolution}-{expected_state}.db")
    bootstrap_content_research_schema(db_path)
    now = "2026-08-19T00:00:00+00:00"
    snapshot_id = f"scv-{resolution}-{expected_state}"
    authorization_id = f"sea-{resolution}-{expected_state}"
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version IN ('0025', '0026')")
        conn.execute("DROP TABLE content_research_execution_facts")
        conn.execute("DROP TABLE content_research_scope_execution_attempts")
        conn.execute("DROP TABLE content_research_scope_execution_units")
        conn.execute(
            "ALTER TABLE content_research_scope_execution_authorizations DROP COLUMN execution_unit_id"
        )
        conn.execute(
            "ALTER TABLE content_research_scope_execution_continuations DROP COLUMN execution_unit_id"
        )
        conn.execute(
            "INSERT INTO content_research_scope_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "rsc-source",
                "run-legacy-matrix",
                "rp-legacy",
                1,
                "v",
                '[{"id":"season","label":"季节","value":"夏季","mode":"required","allowed_aliases":[]}]',
                "[]",
                now,
            ),
        )
        if resulting_scope_id != "rsc-source":
            conn.execute(
                "INSERT INTO content_research_scope_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "rsc-relaxed",
                    "run-legacy-matrix",
                    "rp-legacy",
                    2,
                    "v",
                    '[{"id":"season","label":"季节","value":"夏季","mode":"preferred","allowed_aliases":[]}]',
                    "[]",
                    now,
                ),
            )
        conn.execute(
            """INSERT INTO content_research_scope_coverage_snapshots
               (id, workflow_run_id, scope_contract_id, scope_contract_version,
                execution_revision, execution_authorization_id,
                source_coverage_snapshot_id, state, constraint_counts_json,
                unmet_constraint_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                "run-legacy-matrix",
                "rsc-source",
                1,
                1,
                None,
                None,
                "awaiting_scope_decision",
                "{}",
                '["season"]',
                now,
            ),
        )
        event_payload = {
            "schema_version": "content_research_scope_audit_event_v1",
            "coverage_snapshot_id": snapshot_id,
            "resolution": resolution,
        }
        if target_constraint_id is not None:
            event_payload["constraint_id"] = target_constraint_id
        conn.execute(
            "INSERT INTO content_research_scope_audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"sae-{resolution}-{expected_state}",
                "run-legacy-matrix",
                resulting_scope_id,
                2 if resulting_scope_id == "rsc-relaxed" else 1,
                "coverage_resolved",
                json.dumps(event_payload),
                "{}",
                now,
            ),
        )
        conn.execute(
            "INSERT INTO content_research_scope_execution_authorizations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                authorization_id,
                "run-legacy-matrix",
                resulting_scope_id,
                2 if resulting_scope_id == "rsc-relaxed" else 1,
                snapshot_id,
                resolution,
                1 if resulting_scope_id == "rsc-relaxed" else 2,
                "authorized_limited_report"
                if resolution == "generate_limited_report"
                else "authorized_collection",
                now,
            ),
        )
        conn.execute(
            """INSERT INTO content_research_scope_execution_continuations
               (id, authorization_id, workflow_run_id, execution_revision, operation,
                supplementary_queries_json, state, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                f"sec-{resolution}-{expected_state}",
                authorization_id,
                "run-legacy-matrix",
                1 if resulting_scope_id == "rsc-relaxed" else 2,
                "limited_report"
                if resolution == "generate_limited_report"
                else "supplementary_collection",
                json.dumps(queries, ensure_ascii=False),
                now,
                now,
            ),
        )

    apply_content_research_migrations(db_path, bootstrap_content_research_schema)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """SELECT id, decision_fingerprint, identity_schema, identity_json,
                      identity_state, legacy_authorization_id
               FROM content_research_scope_execution_units"""
        ).fetchone()
    assert row[2] == "execution_decision_identity_v1"
    assert row[4] == expected_state
    assert row[5] == authorization_id
    if expected_state == "canonical":
        expected = build_execution_decision_identity(
            coverage_snapshot_id=snapshot_id,
            source_scope_contract_id="rsc-source",
            resulting_scope_contract_id=resulting_scope_id,
            resolution=resolution,
            target_constraint_id=target_constraint_id,
            supplementary_queries=queries,
        )
        assert row[0] == expected.execution_unit_id
        assert row[1] == expected.decision_fingerprint
        assert row[3] == expected.canonical_json
    else:
        assert row[0].startswith("seu_legacy_")
        store = SQLiteContentResearchStore(db_path)
        snapshot = store.get_coverage_snapshot_by_id(snapshot_id)
        assert snapshot is not None
        with pytest.raises(ValueError, match="legacy_identity_incomplete"):
            store.resolve_coverage_to_execution_unit_atomically(
                snapshot=snapshot,
                decision={
                    "resolution": resolution,
                    "constraint_id": "season",
                    "supplementary_queries": queries,
                },
            )
